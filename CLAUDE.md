# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architecture

Full-stack personal journal analytics app: React SPA + FastAPI backend.

```
/backend                    — FastAPI app (run from this directory)
  main.py                   — lifespan app, CORS, router mounting
  models/journal.py         — SQLModel schema: JournalEntry + engine + get_session
  services/
    llm_client.py           — Anthropic wrapper: async streaming, structured output, retry, cost logging
    embeddings.py           — Chroma vector store: embed_and_store, search_similar
    ingestion.py            — Hash-deduplicated ingest + background mood scoring
  routers/
    ingest.py               — POST /ingest
    synthesis.py            — GET /stream/synthesis  (SSE)
    search.py               — POST /search  (hybrid BM25 + Chroma)
    trends.py               — GET /trends
  prompts/synthesis_v1.yaml — Versioned system + user prompt template

/frontend/src
  api/client.ts             — All fetch calls, base URL from VITE_API_BASE_URL
  hooks/useSSE.ts           — EventSource hook with start/abort control
  hooks/useTrends.ts        — Fetches /trends, cancellable on unmount
  components/
    StreamingPanel.tsx      — SSE rendering, "Stop generating" abort, error state
    MoodChart.tsx           — Recharts 30-day mood line chart
    SearchPanel.tsx         — Hybrid semantic search input + results
    SkeletonLoader.tsx      — Shared loading skeleton
```

## Backend

```bash
cd backend
pip install -r requirements.txt          # first time; use .venv already present
cp .env.example .env                     # add ANTHROPIC_API_KEY
uvicorn main:app --reload
```

**Imports are absolute from `backend/`** — always run `uvicorn` from within `backend/`, not the repo root.

Database: SQLite (`journal.db`) via SQLModel; `engine` and `get_session` live in `models/journal.py` and are imported directly by routers and services.

SSE format: each token is JSON-encoded (`json.dumps(token)`) so newlines survive the `data: …\n\n` SSE frame. The frontend `JSON.parse`s each event.

Mood scoring is a FastAPI `BackgroundTask` — it fires after ingest returns and calls `services/llm_client.py:structured_output`. Failures are silently swallowed.

**Demo mode**: `python scripts/seed_demo.py` builds a self-contained showcase DB (`demo_journal.db` + `demo_chroma_db/`, both gitignored) with ~65 crafted entries across all three sources and a deliberate mood arc. Serve it without touching the real journal via `DATABASE_URL=sqlite:///./demo_journal.db CHROMA_PERSIST_DIR=./demo_chroma_db SYNC_ON_STARTUP=0 uvicorn main:app`.

## Frontend

```bash
cd frontend
npm install                  # adds tailwindcss, recharts, react-markdown, rehype-highlight, zustand
                             # @tailwindcss/vite is also installed (Tailwind v4 Vite plugin)
cp .env.example .env
npm run dev                  # Vite dev server on :5173
npm run build                # tsc -b + vite build
npm run lint                 # ESLint
```

Tailwind v4 is configured via `@import "tailwindcss"` in `index.css` and the `@tailwindcss/vite` plugin in `vite.config.ts` — no `tailwind.config.js` needed.

TypeScript strict flags are on (`noUnusedLocals`, `noUnusedParameters`, `erasableSyntaxOnly`) — unused symbols break the build.

## Key design decisions

- **Stop conditions** (check before doing): modifying `JournalEntry` schema, adding DB migrations, adding auth/user accounts, adding dependencies not in `requirements.txt` / `package.json`.
- **Ingestion stub**: accepts a plain `{"entries": ["…"], "source": "text"}` payload — Google Keep / GoodNotes API auth is not yet implemented.
- **Trends stub**: returns deterministic sinusoidal data when no scored entries exist, so `MoodChart` renders immediately.
- **Future**: multi-user, Postgres, GDPR-compliant isolation. The `source` field and content-hash dedup are designed to be idempotent across future import connectors.

## Skills

Matt Pocock's agent skills are installed under `.agents/skills/` (managed via `skills-lock.json`). Use `/skill-name` in Claude Code to invoke them (e.g. `/prototype`, `/tdd`, `/diagnose`).
