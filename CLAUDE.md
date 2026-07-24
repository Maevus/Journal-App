# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architecture

Full-stack personal journal analytics app: React SPA + FastAPI backend.

```
/backend                    — FastAPI app (run from this directory)
  main.py                   — lifespan app, CORS, router mounting
  models/journal.py         — SQLModel schema: JournalEntry + engine + get_session
  services/
    llm_client.py           — Anthropic wrapper: async streaming, structured output, retry, cost logging. DEFAULT_MODEL = claude-opus-4-8 ($5/$25 per 1M); model + pricing become per-user config once users bring their own key
    embeddings.py           — pgvector store: embed_and_store, search_similar (fastembed MiniLM-384; Postgres-only, no-ops off PG)
    ingestion.py            — Hash-deduplicated ingest + background mood scoring
  routers/
    ingest.py               — POST /ingest
    synthesis.py            — GET /stream/synthesis  (SSE); in-memory LRU cache keyed on the rendered prompt, so identical requests skip the LLM call
    search.py               — POST /search  (hybrid BM25 + pgvector)
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
.venv/bin/pytest                         # run the test suite (from backend/)
```

**Imports are absolute from `backend/`** — always run `uvicorn` (and `pytest`) from within `backend/`, not the repo root.

Tests live in `backend/tests/` (pytest + pytest-asyncio + pytest-cov, `asyncio_mode=auto` via `pytest.ini`; coverage prints on every run). They use an in-memory SQLite DB and mock the external boundaries (the Anthropic client, gkeepapi, the filesystem), so they never touch `journal.db` or the network. The pgvector store is Postgres-only, so `embed_and_store`/`search_similar` no-op off Postgres; real vector behavior is covered by `test_embeddings_integration.py`, gated on `TEST_DATABASE_URL`. Coverage is ~88%: the ingest dedup engine, date parsing, OCR helpers, the Alembic migration/drift guard (`test_migrations.py`), mood scoring, all five router endpoints (via `TestClient` with `get_session` overridden — see the `client`/`add_entry` fixtures in `conftest.py`), and the LLM/embeddings wrappers. Lowest-covered remainder: `main.py` lifespan and the iCloud `brctl` download path.

Database: **Postgres** in real mode (ADR 0003 — the multi-user engine), SQLite for tests + demo. `engine` and `get_session` live in `models/journal.py`; the engine's connect args branch on the URL scheme (`check_same_thread`/`pool_pre_ping`). Point the app at Postgres via `DATABASE_URL=postgresql+psycopg://…` (see `.env.example`); a local server is `docker compose up -d db` (`backend/docker-compose.yml`, `pgvector/pgvector:pg18-trixie` — Postgres 18 + pgvector). **Schema is managed by Alembic** (`backend/alembic/`) — `alembic upgrade head`, and the app also self-migrates on startup (`main._run_migrations`). One-time SQLite→Postgres data copy: `scripts/migrate_sqlite_to_pg.py` (preserves ids); embeddings are (re)built by `scripts/embed_backfill.py` into the pgvector `entry_embeddings` table. Deferred to the hardening phase: RLS + KMS envelope.

SSE format: each token is JSON-encoded (`json.dumps(token)`) so newlines survive the `data: …\n\n` SSE frame. The frontend `JSON.parse`s each event.

Mood scoring is a FastAPI `BackgroundTask` — it fires after ingest returns and calls `services/llm_client.py:structured_output`. Failures are silently swallowed.

**Demo mode**: `python scripts/seed_demo.py` builds a self-contained showcase DB (`demo_journal.db`, gitignored) with ~65 crafted entries across all three sources and a deliberate mood arc. Serve it without touching the real journal via `APP_MODE=demo uvicorn main:app`. The single `APP_MODE` switch (`backend/config.py`) derives the demo db and Keep-sync-off together, so the demo can't accidentally ingest real Keep notes; `DATABASE_URL` / `SYNC_ON_STARTUP` still override individual pieces. **Don't re-pin those in `.env`** — that silently neutralises `APP_MODE=demo`. Semantic search is dormant on the SQLite demo (pgvector is Postgres-only); it returns when the demo becomes a signed-out seed user on Postgres.

**Restarting both servers**: `scripts/dev.sh {demo|real}` (re-runnable; kills the ports first, then starts backend :8000 + frontend :5173). `scripts/dev.sh demo --api-only` skips Vite; `scripts/dev.sh stop` kills both. Logs go to `/tmp/journal-{backend,frontend}.log`.

## Frontend

```bash
cd frontend
npm install                  # adds tailwindcss, recharts, react-markdown, rehype-highlight, zustand
                             # @tailwindcss/vite is also installed (Tailwind v4 Vite plugin)
cp .env.example .env
npm run dev                  # Vite dev server on :5173
npm run build                # tsc -b + vite build
npm run lint                 # ESLint
npm test                     # Vitest (run once); npm run coverage for a report
```

Tailwind v4 is configured via `@import "tailwindcss"` in `index.css` and the `@tailwindcss/vite` plugin in `vite.config.ts` — no `tailwind.config.js` needed.

TypeScript strict flags are on (`noUnusedLocals`, `noUnusedParameters`, `erasableSyntaxOnly`) — unused symbols break the build.

Tests use Vitest + Testing Library (jsdom) — config lives in `vite.config.ts` (`test` block) with `src/test/setup.ts` for jest-dom matchers. `*.test.ts(x)` files are excluded from `tsc -b` (see `tsconfig.app.json`) so the production build stays test-free. Covered: `api/client.ts`, the `useSSE`/`useTrends` hooks (EventSource/fetch mocked), and the `DayDetail`/`StreamingPanel` components. Untested so far: `MoodChart`, `SearchPanel`, the selectors, `App`.

## Key design decisions

- **Stop conditions** (check before doing): modifying `JournalEntry` schema, adding auth/user accounts, adding dependencies not in `requirements.txt` / `package.json`. (Schema changes now go through an Alembic revision — `alembic revision --autogenerate` — rather than the removed `ensure_schema` shim; `tests/test_migrations.py` guards model/migration drift.)
- **Ingestion stub**: accepts a plain `{"entries": ["…"], "source": "text"}` payload — Google Keep / GoodNotes API auth is not yet implemented.
- **Trends stub**: returns deterministic sinusoidal data when no scored entries exist, so `MoodChart` renders immediately.
- **Future**: multi-user, Postgres, GDPR-compliant isolation. The `source` field and content-hash dedup are designed to be idempotent across future import connectors.

## Skills

Matt Pocock's agent skills are installed under `.agents/skills/` (managed via `skills-lock.json`). Use `/skill-name` in Claude Code to invoke them (e.g. `/prototype`, `/tdd`, `/diagnose`).
