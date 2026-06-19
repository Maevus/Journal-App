# Chatbot: ask questions about your journal (RAG, V1)

> **Status:** approved 2026-06-19, **not yet implemented**. Build this in a fresh
> session (a long planning session made context expensive). Todo #12.

## Context

Let the user ask natural-language questions about their journal and get a streamed,
grounded answer ("what did I write about climbing?", "summarise my mood in May"). This is
**RAG, and both halves already exist** — so it's mostly composition, not new architecture:
- **Retrieval:** the hybrid search in `backend/routers/search.py` (semantic via
  `services/embeddings.py:search_similar` + BM25 over content, fused 0.7/0.3) already finds
  the most relevant entries for a query.
- **Generation + streaming:** `services/llm_client.py:stream_text` + the SSE pattern in
  `backend/routers/synthesis.py` + `frontend/src/hooks/useSSE.ts` already stream an LLM
  answer over entries (the synthesis panel).

**V1 scope (confirmed): single-turn** (no chat history) + **inline citations** (model
references entry dates in prose). No multi-turn, no structured source list, no caching.

## Approach

### Backend
1. **Extract shared retrieval** — the hybrid fusion is currently inline in
   `routers/search.py`. Factor it into `backend/services/retrieval.py`:
   `hybrid_search(session, query, limit) -> list[tuple[JournalEntry, float]]`
   (semantic `search_similar` + `rank_bm25.BM25Okapi` over `entry.content`, existing 0.7/0.3
   weighting, top-K). Refactor `/search` to call it (behaviour unchanged — its tests must
   stay green). `/chat` reuses it. Reuse, don't duplicate the fusion.
2. **`backend/prompts/chat_v1.yaml`** — mirror `synthesis_v1.yaml` (versioned `system` +
   `user_template`). System: warm, grounded, ADHD-aware; **cite entry dates inline**; answer
   only from the provided entries and say so when they don't cover the question.
   `user_template` has `{entries}` and `{question}`.
3. **`backend/routers/chat.py`** — `GET /chat?q=<question>&limit=<k>` SSE, mirroring
   `synthesis.py` exactly (the `event_stream()` generator, `data: {json.dumps(token)}` +
   `data: [DONE]`, same `StreamingResponse` headers):
   - `hybrid_search(session, q, limit≈8)` → format entries with their dates (like synthesis
     builds `entries_text`).
   - `stream_text(prompt=user_template.format(...), system=cfg["system"],
     model=cfg.get("model", DEFAULT_MODEL), max_tokens≈2048)`.
   - Empty/blank `q` → empty stream (just `[DONE]`) or 400; no retrieval.
   - **No cache.** Retrieval over **all** entries — chat ignores the global range/source
     filters (a question defines its own scope).
   - Mount in `main.py`: `app.include_router(chat.router, prefix="/stream", tags=["chat"])`
     → `GET /stream/chat` (same prefix as synthesis; define `@router.get("/chat")`).

### Frontend
4. **`chatUrl(q, limit)`** in `api/client.ts` (like `synthesisUrl`) → `/stream/chat?q=…`.
5. **`frontend/src/components/ChatPanel.tsx`** — input + "Ask" button (SearchPanel's UX) +
   streamed answer rendered with `ReactMarkdown` (StreamingPanel's render). On submit, set
   the question → `useSSE(chatUrl(q))` → `start()`. Single-turn: show the current question
   and its streaming answer (a small client-side Q/A list is optional polish; no history is
   sent to the backend). Reuse `useSSE`'s `start`/`abort`/`isStreaming`/`error`.
6. **Place it in `App.tsx`** — add an "Ask" panel as a new section (e.g. full-width below
   the synthesis/search grid), with a sticker label like the others.

## Non-goals (V2+)
Multi-turn/history, structured clickable citations, response caching, honoring the global
range/source filters in chat.

## Reuse (don't reinvent)
`synthesis.py` (SSE generator + `_load_prompt` lru_cache), `stream_text`, `search_similar` +
the fusion from `search.py`, `useSSE`, `ReactMarkdown`, `SearchPanel` (input UX) +
`StreamingPanel` (streaming render + the mocked-`useSSE` test pattern), `synthesis_v1.yaml`
structure, and the test fixtures (`client`/`add_entry` in conftest; `FakeEventSource` in
`useSSE.test.ts`).

## Tests
- **Backend** `tests/test_chat_api.py` (mock `hybrid_search` + `stream_text` via the
  `client` fixture): `/stream/chat?q=…` streams answer tokens + `[DONE]`; retrieval called
  with the question; blank `q` short-circuits (no `stream_text` call). Add a `hybrid_search`
  unit test and confirm the **`/search` refactor keeps its existing tests green**.
- **Frontend**: `chatUrl` test in `client.test.ts`; a `ChatPanel` test mirroring
  `StreamingPanel.test.tsx` (mock `useSSE`: renders streamed answer, calls `start` on Ask).

## Verification
- `cd backend && .venv/bin/python -m pytest` green (chat + unchanged search). Frontend
  `npm test` + `npm run build` green.
- Manual: `GET /stream/chat?q=what did I write about climbing` streams a grounded answer
  citing dates; a question with no relevant entries → the model says so. In the app, the
  Ask panel streams the answer live.

## Execution (worktree split, as used for export/goodnotes)
Backend (`retrieval.py`, `chat.py`, `chat_v1.yaml`, `main.py`, backend tests) via a
background **isolated-worktree agent** — symlink the shared `backend/.venv` and use
`dangerouslyDisableSandbox` for the test runs (a fresh worktree has no venv; the absolute
venv path is otherwise sandbox-blocked). Frontend (`chatUrl`, `ChatPanel`, `App.tsx`,
frontend tests) in the **main tree** (where `node_modules` lives), then merge the backend
branch into `main` and wire the UI to it. Conventions: `clock.utcnow_naive()` not
`datetime.utcnow`; `git -C <repo>` never `cd` then git.
