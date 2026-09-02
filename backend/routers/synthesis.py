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
from pydantic import BaseModel
from sqlmodel import Session

from clock import utcnow_naive
from models.journal import JournalEntry, get_session, user_entries
from models.user import User
from services.auth import get_current_user
from services.llm_client import (
    LLMProviderError,
    resolve_model,
    sse_llm_error_frame,
    stream_text,
)
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

# In-memory LRU cache keyed on the fully-rendered prompt (embeds entries,
# range, source), so any edit/add or parameter change regenerates. Resets on
# restart; bounded so it can't grow unbounded.
_CACHE: "OrderedDict[str, str]" = OrderedDict()
_CACHE_MAX = 32


@lru_cache(maxsize=1)
def _load_prompt() -> dict:
    with open(_PROMPT_PATH) as fh:
        return yaml.safe_load(fh)


def _cache_key(user_id: int, model: str, max_tokens: int, system: str, user_prompt: str) -> str:
    # user_id is in the key too: not leaking across users should be a property
    # of the key itself, not an accident of what the prompt happens to embed.
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


class SynthesisRequest(BaseModel):
    """The synthesis scope, carried in the request body rather than the query
    string — see the route docstring for why this is a POST at all."""

    limit: int = 20
    days: int = 0  # calendar window; 0 = all time
    source: str | None = None  # "google_keep" | "goodnotes" | "text"; None = all


@router.post("/synthesis")
async def synthesis(
    body: SynthesisRequest,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
    limit_decision: RateLimitDecision = Depends(stream_rate_limit(TIER_SYNTHESIS)),
):
    """Stream a synthesis of the user's entries as SSE.

    POST, deliberately: as a GET, this was a paid Opus call reachable by
    cross-site navigation — `SameSite=Lax` still sends the session cookie on a
    top-level GET, and CORS blocks reading the response but not processing the
    request. A third-party page could burn a logged-in user's budget (or a
    BYO-key user's own Anthropic spend) via an iframe or link.

    A POST with a JSON body isn't a CORS "simple request", so the browser must
    preflight it — main.py's allow-list (config.FRONTEND_URL only) refuses that
    preflight, closing the cross-site path. The scope lives in the body for the
    same reason: form-encoded would be a simple request. The rate limit below
    is defense in depth on top of that.
    """
    # 200 with a named frame, not 429 — EventSource can't read a 4xx body (same
    # shape as the quota refusal below). Checked first so a refused caller costs nothing.
    if limit_decision.refused:
        return rate_limited_stream(limit_decision)

    query = user_entries(user.id).order_by(JournalEntry.timestamp.asc())
    if body.days > 0:
        # naive UTC to match JournalEntry.timestamp (stored naive) — aware would break >=. See clock.utcnow_naive.
        cutoff = utcnow_naive() - timedelta(days=body.days)
        query = query.where(JournalEntry.timestamp >= cutoff)
    if body.source:
        query = query.where(JournalEntry.source == body.source)
    entries = list(session.exec(query).all())

    # Sample evenly across the window so long ranges reflect the whole period, not just recent entries.
    if len(entries) > body.limit:
        step = len(entries) / body.limit
        entries = [entries[int(i * step)] for i in range(body.limit)]

    cfg = _load_prompt()

    if entries:
        period = f"the last {body.days} days" if body.days > 0 else "all time"
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
    # F3: model is per-user, not the prompt-YAML value. Resolved here so it's
    # both what runs and part of the cache key.
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

        # Miss: stream and accumulate; only cache once complete, so a mid-stream failure stores nothing.
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
            # Nothing is cached: the refusal is about today's budget, not this prompt's answer.
            yield sse_quota_frame(exc)
            yield "data: [DONE]\n\n"
            return
        except LLMProviderError as exc:
            # Nothing is cached, as with quota: a provider failure isn't this
            # prompt's answer.
            yield sse_llm_error_frame(exc)
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
