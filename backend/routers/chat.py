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
from models.user import User
from services.auth import get_current_user
from services.llm_client import resolve_model, stream_text
from services.quota import QuotaExceededError, sse_quota_frame
from services.retrieval import (
    condense_query,
    hybrid_search,
    mood_summary,
    parse_date_range,
    render_mood_summary,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "chat_v2.yaml"

# How much of an entry to show in a citation chip — enough to recognise it,
# not the whole entry.
_SNIPPET_CHARS = 200


def _mood_tag(score: float | None) -> str:
    """The per-entry mood shown to the agent — the computed 0-1 sentiment, or
    'unscored' for an entry the background scorer hasn't reached."""
    return f"{score:.2f}" if score is not None else "unscored"

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
    user: User = Depends(get_current_user),
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
    # Drop any empty-content turns: the Messages API rejects empty non-final
    # content, and an aborted/errored turn can leave one in the client history.
    history = [
        {"role": m.role, "content": m.content}
        for m in messages[:-1]
        if m.content.strip()
    ]
    standalone = await condense_query(history, question, user_id=user.id)

    # Time-scoped questions constrain retrieval to that window; non-scoped get
    # (None, None) and retrieve over the whole journal.
    start, end = await parse_date_range(standalone, user_id=user.id)
    hits = hybrid_search(session, standalone, body.limit, start=start, end=end,
                         user_id=user.id)
    # A window that matches nothing (a period the journal doesn't cover, or a
    # mis-parse) shouldn't make the bot claim the journal is empty — fall back to
    # time-unscoped (still user-scoped) retrieval so it answers from what exists.
    if not hits and (start is not None or end is not None):
        hits = hybrid_search(session, standalone, body.limit, user_id=user.id)

    cfg = _load_prompt()

    if hits:
        entries_text = "\n---\n".join(
            f"[{i}] [{entry.timestamp.date()} | source: {entry.source} | "
            f"mood: {_mood_tag(entry.mood_score)}]\n{entry.content}"
            for i, (entry, _) in enumerate(hits, start=1)
        )
    else:
        entries_text = "No journal entries found."

    # Mood aggregate so the agent can answer average/trend questions the ~limit
    # retrieved entries can't cover — scoped to the same window retrieval used,
    # so a time-scoped question ("mood in 2024?") summarises that period and an
    # unscoped one summarises the whole journal. Omitted when nothing's scored
    # in scope.
    summary = mood_summary(session, user_id=user.id, start=start, end=end)
    summary_block = f"{render_mood_summary(summary)}\n\n" if summary else ""

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

    user_prompt = cfg["user_template"].format(
        mood_summary=summary_block, entries=entries_text, question=question
    )
    # Prior turns as real user/assistant messages, then the current question as a
    # user turn carrying the retrieved entries.
    llm_messages = history + [{"role": "user", "content": user_prompt}]
    model = resolve_model(user_id=user.id)  # F3: per-user, not the prompt-YAML value
    max_tokens = cfg.get("max_tokens", 2048)

    async def event_stream():
        try:
            async for token in stream_text(
                messages=llm_messages,
                system=cfg["system"],
                model=model,
                max_tokens=max_tokens,
                user_id=user.id,
            ):
                # JSON-encode each token so newlines in the payload survive the SSE frame.
                yield f"data: {json.dumps(token)}\n\n"
        except QuotaExceededError as exc:
            # No sources frame after a refusal — citation chips for an answer
            # that never happened would be orphans.
            yield sse_quota_frame(exc)
            yield "data: [DONE]\n\n"
            return
        # Structured sources frame (named event) after the text, before [DONE].
        yield f"event: sources\ndata: {json.dumps(sources)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(), media_type="text/event-stream", headers=_SSE_HEADERS
    )
