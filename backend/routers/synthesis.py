import hashlib
import json
import logging
from collections import OrderedDict
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlmodel import Session

from clock import utcnow_naive
from models.journal import JournalEntry, get_session, user_entries
from models.user import User
from services.auth import get_current_user
from services.llm_client import resolve_model, stream_text
from services.quota import QuotaExceededError, sse_quota_frame
from services.rate_limit import (
    TIER_SYNTHESIS,
    RateLimitDecision,
    rate_limited_stream,
    stream_rate_limit,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "synthesis_v1.yaml"

# In-memory synthesis cache: an identical request reuses the generated text
# instead of spending another LLM call. The key is the fully-rendered prompt,
# which embeds the selected entries, range, and source — so any entry edit/add
# or parameter change produces a different key and regenerates. Resets on
# restart; bounded (LRU) so it can't grow without limit.
_CACHE: "OrderedDict[str, str]" = OrderedDict()
_CACHE_MAX = 32


@lru_cache(maxsize=1)
def _load_prompt() -> dict:
    with open(_PROMPT_PATH) as fh:
        return yaml.safe_load(fh)


def _cache_key(user_id: int, model: str, max_tokens: int, system: str, user_prompt: str) -> str:
    # user_id is in the key even though the rendered prompt already embeds the
    # user's own entries: not-leaking across users should be a property of the
    # key itself, not an accident of prompt contents.
    version = _load_prompt().get("version", "?")
    raw = "\x00".join([str(version), str(user_id), model, str(max_tokens), system, user_prompt])
    return hashlib.sha256(raw.encode()).hexdigest()


def _cache_get(key: str) -> str | None:
    text = _CACHE.get(key)
    if text is not None:
        _CACHE.move_to_end(key)  # mark most-recently-used
    return text


def _cache_put(key: str, text: str) -> None:
    _CACHE[key] = text
    _CACHE.move_to_end(key)
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)  # evict least-recently-used


@router.get("/synthesis")
async def synthesis(
    limit: int = 20,
    days: int = 0,  # calendar window; 0 = all time
    source: str | None = None,  # "google_keep" | "goodnotes" | "text"; None = all
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
    limit_decision: RateLimitDecision = Depends(stream_rate_limit(TIER_SYNTHESIS)),
):
    # Rate limit (services/rate_limit.py). A 200 carrying a named frame rather
    # than a 429, because EventSource can't read a 4xx body — same shape as the
    # quota refusal below. Checked before the entry query so a refused caller
    # costs nothing.
    if limit_decision.refused:
        return rate_limited_stream(limit_decision)

    query = user_entries(user.id).order_by(JournalEntry.timestamp.asc())
    if days > 0:
        # naive UTC to match JournalEntry.timestamp (stored naive); an aware
        # datetime would break the >= comparison. See clock.utcnow_naive.
        cutoff = utcnow_naive() - timedelta(days=days)
        query = query.where(JournalEntry.timestamp >= cutoff)
    if source:
        query = query.where(JournalEntry.source == source)
    entries = list(session.exec(query).all())

    # Sample evenly across the window so long ranges reflect the whole period
    # rather than just the `limit` most recent entries.
    if len(entries) > limit:
        step = len(entries) / limit
        entries = [entries[int(i * step)] for i in range(limit)]

    cfg = _load_prompt()

    if entries:
        period = f"the last {days} days" if days > 0 else "all time"
        entries_text = (
            f"(Entries spanning {period}, in chronological order.)\n\n"
            + "\n---\n".join(
                f"[{e.timestamp.date()} | source: {e.source}]\n{e.content}"
                for e in entries
            )
        )
    else:
        entries_text = "No journal entries found yet."

    user_prompt = cfg["user_template"].format(entries=entries_text)
    # F3: the model is per-user now, not the prompt-YAML value. Resolve it here
    # so it's both what we run and part of the cache key — a user's cached
    # synthesis is keyed to the model that produced it.
    model = resolve_model(user_id=user.id)
    max_tokens = cfg.get("max_tokens", 1024)
    key = _cache_key(user.id, model, max_tokens, cfg["system"], user_prompt)

    async def event_stream():
        cached = _cache_get(key)
        if cached is not None:
            logger.info("synthesis cache hit %s — no LLM call", key[:8])
            yield f"data: {json.dumps(cached)}\n\n"
            yield "data: [DONE]\n\n"
            return

        # Miss: stream from the LLM, accumulating tokens. Only cache once the
        # stream completes — a mid-stream failure propagates and stores nothing.
        chunks: list[str] = []
        try:
            async for token in stream_text(
                prompt=user_prompt,
                system=cfg["system"],
                model=model,
                max_tokens=max_tokens,
                user_id=user.id,
            ):
                chunks.append(token)
                # JSON-encode each token so newlines in the payload are safe in SSE
                yield f"data: {json.dumps(token)}\n\n"
        except QuotaExceededError as exc:
            # Nothing is cached: the refusal is about today's budget, not
            # this prompt's answer.
            yield sse_quota_frame(exc)
            yield "data: [DONE]\n\n"
            return
        _cache_put(key, "".join(chunks))
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
