import json
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
    session: Session = Depends(get_session),
):
    entries = session.exec(
        select(JournalEntry).order_by(JournalEntry.timestamp.desc()).limit(limit)
    ).all()

    cfg = _load_prompt()

    if entries:
        entries_text = "\n---\n".join(
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
