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
from sqlmodel import Session, select

from clock import utcnow_naive
from models.journal import JournalEntry, get_session
from services.llm_client import DEFAULT_MODEL, stream_text

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


def _cache_key(model: str, max_tokens: int, system: str, user_prompt: str) -> str:
    version = _load_prompt().get("version", "?")
    raw = "\x00".join([str(version), model, str(max_tokens), system, user_prompt])
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
):
    query = select(JournalEntry).order_by(JournalEntry.timestamp.asc())
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
    model = cfg.get("model", DEFAULT_MODEL)
    max_tokens = cfg.get("max_tokens", 1024)
    key = _cache_key(model, max_tokens, cfg["system"], user_prompt)

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
        async for token in stream_text(
            prompt=user_prompt,
            system=cfg["system"],
            model=model,
            max_tokens=max_tokens,
        ):
            chunks.append(token)
            # JSON-encode each token so newlines in the payload are safe in SSE
            yield f"data: {json.dumps(token)}\n\n"
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
