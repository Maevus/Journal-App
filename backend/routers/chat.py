import json
import logging
from functools import lru_cache
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlmodel import Session

from models.journal import get_session
from services.llm_client import DEFAULT_MODEL, stream_text
from services.retrieval import hybrid_search, parse_date_range

logger = logging.getLogger(__name__)

router = APIRouter()

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "chat_v1.yaml"

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


@lru_cache(maxsize=1)
def _load_prompt() -> dict:
    with open(_PROMPT_PATH) as fh:
        return yaml.safe_load(fh)


@router.get("/chat")
async def chat(
    q: str = "",
    limit: int = 8,
    session: Session = Depends(get_session),
):
    """Single-turn RAG over the whole journal: retrieve the most relevant
    entries for `q`, then stream a grounded answer (dates cited inline).

    Chat ignores the global range/source filters — a question defines its own
    scope. No response cache (unlike synthesis): each question is one-off."""
    question = q.strip()

    async def empty_stream():
        yield "data: [DONE]\n\n"

    # Blank query short-circuits: no retrieval, no LLM call.
    if not question:
        return StreamingResponse(
            empty_stream(), media_type="text/event-stream", headers=_SSE_HEADERS
        )

    # Time-scoped questions ("energy in May") constrain retrieval to that window
    # so date-relevant entries aren't crowded out; non-scoped questions get
    # (None, None) and retrieve over the whole journal.
    start, end = await parse_date_range(question)
    hits = hybrid_search(session, question, limit, start=start, end=end)
    # A window that matches nothing (e.g. a period the journal doesn't cover, or a
    # mis-parse) shouldn't make the bot claim the journal is empty — fall back to
    # unscoped retrieval so it still answers from whatever exists.
    if not hits and (start is not None or end is not None):
        hits = hybrid_search(session, question, limit)
    cfg = _load_prompt()

    if hits:
        entries_text = "\n---\n".join(
            f"[{entry.timestamp.date()} | source: {entry.source}]\n{entry.content}"
            for entry, _ in hits
        )
    else:
        entries_text = "No journal entries found."

    user_prompt = cfg["user_template"].format(entries=entries_text, question=question)
    model = cfg.get("model", DEFAULT_MODEL)
    max_tokens = cfg.get("max_tokens", 2048)

    async def event_stream():
        async for token in stream_text(
            prompt=user_prompt,
            system=cfg["system"],
            model=model,
            max_tokens=max_tokens,
        ):
            # JSON-encode each token so newlines in the payload survive the SSE frame.
            yield f"data: {json.dumps(token)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(), media_type="text/event-stream", headers=_SSE_HEADERS
    )
