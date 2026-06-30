import json
import logging
from functools import lru_cache
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session

from models.journal import get_session
from services.llm_client import DEFAULT_MODEL, stream_text
from services.retrieval import condense_query, hybrid_search, parse_date_range

logger = logging.getLogger(__name__)

router = APIRouter()

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "chat_v2.yaml"

# How much of an entry to show in a citation chip — enough to recognise it,
# not the whole entry.
_SNIPPET_CHARS = 200

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


@lru_cache(maxsize=1)
def _load_prompt() -> dict:
    with open(_PROMPT_PATH) as fh:
        return yaml.safe_load(fh)


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    limit: int = 8


@router.post("/chat")
async def chat(
    body: ChatRequest,
    session: Session = Depends(get_session),
):
    """Multi-turn RAG over the whole journal. The client holds the conversation
    and POSTs the full message history each turn; the server stays stateless.

    For each turn we condense the latest question (with prior turns as context)
    into a standalone query, retrieve the most relevant entries for it, then
    stream a grounded answer that cites entries inline with [n] markers. After
    the text, a single `event: sources` frame carries the numbered entries so the
    frontend can render clickable citation chips. Chat ignores the global
    range/source filters — a question defines its own scope."""
    messages = body.messages
    last = messages[-1] if messages else None
    question = last.content.strip() if last and last.role == "user" else ""

    async def empty_stream():
        yield "data: [DONE]\n\n"

    # Blank / malformed last turn short-circuits: no retrieval, no LLM call.
    if not question:
        return StreamingResponse(
            empty_stream(), media_type="text/event-stream", headers=_SSE_HEADERS
        )

    # Prior turns (everything before the current question) give condensation the
    # context to resolve follow-ups like "and June?" into a standalone query.
    history = [{"role": m.role, "content": m.content} for m in messages[:-1]]
    standalone = await condense_query(history, question)

    # Time-scoped questions constrain retrieval to that window; non-scoped get
    # (None, None) and retrieve over the whole journal.
    start, end = await parse_date_range(standalone)
    hits = hybrid_search(session, standalone, body.limit, start=start, end=end)
    # A window that matches nothing (a period the journal doesn't cover, or a
    # mis-parse) shouldn't make the bot claim the journal is empty — fall back to
    # unscoped retrieval so it still answers from whatever exists.
    if not hits and (start is not None or end is not None):
        hits = hybrid_search(session, standalone, body.limit)

    cfg = _load_prompt()

    if hits:
        entries_text = "\n---\n".join(
            f"[{i}] [{entry.timestamp.date()} | source: {entry.source}]\n{entry.content}"
            for i, (entry, _) in enumerate(hits, start=1)
        )
    else:
        entries_text = "No journal entries found."

    # Sources side-channel: the full numbered set, sent after the text so the
    # frontend can render a chip for each [n] the answer actually used.
    sources = [
        {
            "n": i,
            "id": entry.id,
            "date": entry.timestamp.date().isoformat(),
            "source": entry.source,
            "snippet": entry.content[:_SNIPPET_CHARS],
        }
        for i, (entry, _) in enumerate(hits, start=1)
    ]

    user_prompt = cfg["user_template"].format(entries=entries_text, question=question)
    # Prior turns as real user/assistant messages, then the current question as a
    # user turn carrying the retrieved entries.
    llm_messages = history + [{"role": "user", "content": user_prompt}]
    model = cfg.get("model", DEFAULT_MODEL)
    max_tokens = cfg.get("max_tokens", 2048)

    async def event_stream():
        async for token in stream_text(
            messages=llm_messages,
            system=cfg["system"],
            model=model,
            max_tokens=max_tokens,
        ):
            # JSON-encode each token so newlines in the payload survive the SSE frame.
            yield f"data: {json.dumps(token)}\n\n"
        # Structured sources frame (named event) after the text, before [DONE].
        yield f"event: sources\ndata: {json.dumps(sources)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(), media_type="text/event-stream", headers=_SSE_HEADERS
    )
