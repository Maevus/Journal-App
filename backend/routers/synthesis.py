import json
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from models.journal import JournalEntry, get_session
from services.llm_client import stream_text

router = APIRouter()

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "synthesis_v1.yaml"


@lru_cache(maxsize=1)
def _load_prompt() -> dict:
    with open(_PROMPT_PATH) as fh:
        return yaml.safe_load(fh)


@router.get("/synthesis")
async def synthesis(
    limit: int = 20,
    days: int = 0,  # calendar window; 0 = all time
    session: Session = Depends(get_session),
):
    query = select(JournalEntry).order_by(JournalEntry.timestamp.asc())
    if days > 0:
        cutoff = datetime.utcnow() - timedelta(days=days)
        query = query.where(JournalEntry.timestamp >= cutoff)
    entries = list(session.exec(query).all())

    # Sample evenly across the window so long ranges reflect the whole period
    # rather than just the `limit` most recent entries.
    if len(entries) > limit:
        step = len(entries) / limit
        entries = [entries[int(i * step)] for i in range(limit)]

    cfg = _load_prompt()

    if entries:
        period = f"the last {days} days" if days > 0 else "all time"
        entries_text = f"(Entries spanning {period}, in chronological order.)\n\n" + "\n---\n".join(
            f"[{e.timestamp.date()} | source: {e.source}]\n{e.content}"
            for e in entries
        )
    else:
        entries_text = "No journal entries found yet."

    user_prompt = cfg["user_template"].format(entries=entries_text)

    async def event_stream():
        async for token in stream_text(
            prompt=user_prompt,
            system=cfg["system"],
            model=cfg.get("model", "claude-opus-4-5"),
            max_tokens=cfg.get("max_tokens", 1024),
        ):
            # JSON-encode each token so newlines in the payload are safe in SSE
            yield f"data: {json.dumps(token)}\n\n"
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
