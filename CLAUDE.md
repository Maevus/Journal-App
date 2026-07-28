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

**Demo**: `scripts/seed_demo.py` seeds ~65 crafted entries (all three sources, a deliberate mood arc) as a **`demo` user**. Demo-ness is **per-user** (`services/auth.py:is_demo_user`), not a global flag, so one instance serves the demo visitor and real users at once. Two ways to run it:
- **Signed-out demo of the one app (shape B, the hosting shape)**: seed into the shared Postgres (`DATABASE_URL=…pg… python scripts/seed_demo.py`), then run with `PUBLIC_DEMO=1` — a logged-out visitor resolves to the demo user (read-only: `/ingest` + `/keys` 403; chat/search open, bounded by `DEMO_DAILY_USD`), and signing in gives them their own journal. `seed_demo`'s wipe is **demo-user-scoped** (never touches other users). Semantic search works (pgvector embeddings seeded).
- **Standalone SQLite demo**: `APP_MODE=demo` resolves *every* request to the demo user against `demo_journal.db` (search dormant — pgvector is Postgres-only). The `APP_MODE` switch derives the demo db + Keep-sync-off together; **don't re-pin `DATABASE_URL`/`SYNC_ON_STARTUP` in `.env`** or it neutralises `APP_MODE=demo`.

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

Matt Pocock's agent skills are installed under `.claude/skills/` (managed via `skills-lock.json`). Use `/skill-name` in Claude Code to invoke them (e.g. `/prototype`, `/tdd`, `/diagnose`).
