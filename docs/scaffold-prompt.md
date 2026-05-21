## Objective
Scaffold a full-stack personal journal analytics application — React frontend + FastAPI backend — that ingests journal entries from Google Keep and GoodNotes, runs LLM synthesis on them, and displays mood trends, goal tracking, and semantic search in a dashboard UI.

## Context
- This is a greenfield project. Nothing exists yet.
- The user manages ADHD and is building this to surface behavioural patterns over time using LLM analysis.
- The user intends to make this app available to other ADHD journal users over time via  
- Stack is fixed: React (Vite, TypeScript, Tailwind, Recharts) + FastAPI (Python 3.11+, SQLModel, Chroma for vectors, Anthropic SDK).
- The LLM integration must stream responses — no blocking calls.
- All API keys and secrets go in .env files only, never in code.

## Target State
A working monorepo with this structure:
/backend
  main.py              — FastAPI app entrypoint
  routers/
    ingest.py          — POST /ingest
    synthesis.py       — GET /stream/synthesis (SSE)
    search.py          — POST /search
    trends.py          — GET /trends
  models/
    journal.py         — SQLModel schema: JournalEntry (id, source, content, timestamp, mood_score, content_hash)
  services/
    llm_client.py      — Hardened Anthropic wrapper: streaming, structured output, retry, token tracking
    embeddings.py      — Embed entries, store/query Chroma
    ingestion.py       — Parse and normalise entries, idempotent (content hash dedup)
  prompts/
    synthesis_v1.yaml  — Versioned synthesis prompt
  .env.example
  requirements.txt

/frontend
  src/
    components/
      StreamingPanel.tsx   — Renders SSE stream token by token with abort control
      MoodChart.tsx        — Recharts line chart, 30-day rolling mood scores
      SearchPanel.tsx      — Semantic search input + results with highlighted context
      SkeletonLoader.tsx   — Shared loading skeleton
    hooks/
      useSSE.ts            — Custom hook: EventSource with abort controller
      useTrends.ts         — Fetch + cache /trends data
    api/
      client.ts            — All fetch calls, base URL from env
  .env.example

## Scope
- Work only in /backend and /frontend directories
- Do NOT create Docker, CI, or deployment configs — that comes later
- Do NOT implement Google Keep or GoodNotes API auth yet — stub the ingestion service to accept a plain text array of entries as input

## Constraints
- Python: FastAPI with lifespan context manager (not @app.on_event, which is deprecated)
- React: functional components only, no class components
- No new dependencies beyond what's listed in requirements.txt and package.json below
- llm_client.py MUST implement: async streaming, Pydantic-validated structured output, exponential backoff (max 3 retries), per-call token + cost logging to stdout
- StreamingPanel.tsx MUST handle: token-by-token rendering, "Stop generating" abort button, error state display
- Only make changes directly requested. Do not add features or abstractions beyond what was asked.

requirements.txt:
fastapi, uvicorn, sqlmodel, anthropic, chromadb, httpx, python-dotenv, pydantic, apscheduler, rank_bm25

package.json dependencies:
react, react-dom, typescript, vite, tailwindcss, recharts, react-markdown, rehype-highlight, zustand

## Acceptance Criteria
- [ ] FastAPI app starts with uvicorn and all 4 routes respond (even with stub data)
- [ ] GET /stream/synthesis returns a valid SSE stream with at least one data: event
- [ ] StreamingPanel.tsx renders streamed tokens progressively and the abort button cancels the stream
- [ ] MoodChart.tsx renders with stub data (array of {date, score} objects)
- [ ] POST /search returns ranked results with similarity scores
- [ ] llm_client.py logs token count and estimated cost ($) to stdout on every call
- [ ] No API keys hardcoded anywhere — all read from os.environ

## Stop Conditions
Stop and ask before:
- Adding any dependency not listed above
- Modifying the JournalEntry schema after first creation
- Making any database migration
- Adding authentication or user accounts

## Progress
After each completed step: ✅ [what was done] — [file(s) affected]

Think carefully before starting. Lay out your implementation plan in one paragraph, then begin.


## Future
- Intended as a public product for ADHD users
- Will need multi-user auth, Postgres, per-user data isolation
- Privacy-sensitive data — GDPR relevant (user is in EU)