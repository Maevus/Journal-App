# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Global Claude Code Instructions

## Preferred Tools & Auto-Install via Homebrew

Before falling back to Python scripts or sed for file manipulation, check
for and prefer these tools. If a preferred tool is not installed, suggest
installing it via Homebrew before proceeding with an inferior alternative.

### File Editing
- **Edit tool** — always first choice for single precise edits
- **fastmod** — bulk pattern replacement across files
  - Check: `which fastmod`
  - Install: `brew install fastmod`
  - Run directly: `fastmod 'pattern' 'replacement' dir/` — do NOT wrap in
    shell scaffolding (2>&1, echo exit codes, etc.) as this breaks pre-approval

### Data Formats
- **jq** — JSON querying and manipulation
  - Check: `which jq`
  - Install: `brew install jq`
- **yq** — YAML querying and manipulation
  - Check: `which yq`
  - Install: `brew install yq`
- **yamllint** — YAML validation and linting; always use instead of Python for YAML validation
  - Check: `which yamllint`
  - Install: `brew install yamllint`

### Search
- **ripgrep (rg)** — fast file content search, use before Edit to confirm
  exact match strings
  - Check: `which rg`
  - Install: `brew install ripgrep`

### Text Replacement
- **sd** — modern sed alternative with sane regex syntax, pre-approved in settings.json
  - Check: `which sd`
  - Install: `brew install sd`

### Behavior
- If a preferred tool is missing, say so and offer to install it via
  Homebrew before falling back to Python or sed
- Never write a temporary Python script solely to edit or transform files
  when the above tools are available
- Never use sed — use sd instead (it is pre-approved and will not prompt)

## Bash Tool — Exit Codes and Output Capture

Claude Code's Bash tool automatically returns stdout, stderr, and the exit
code as part of every tool result. Therefore:
- Never append `echo "exit: $?"` — the exit code is already available
- Never use `2>&1` to capture stderr — it is already captured separately
- Never wrap commands in diagnostic scaffolding just to observe results
- Run commands directly: `fastmod 'a' 'b' src/` not `fastmod 'a' 'b' src/ 2>&1 && echo $?`
- Wrapping pre-approved tools in shell expressions breaks pattern matching
  and causes unnecessary approval prompts

## File Editing — Approval Avoidance

The Edit and Write tools require NO Bash execution and NO approval prompts.
Bash-based tools ALL require approval unless pre-approved in settings.json.

### Decision rule — follow strictly:
1. Single location edit → use Edit tool directly. Never use Bash.
2. Whole file rewrite → use Write tool. Never use Bash.
3. Bulk edits across multiple files → use Edit tool in a loop across
   files. Still no Bash.
4. Only use sd or fastmod when regex pattern matching is genuinely
   required AND the Edit tool cannot work. Both are pre-approved.

### The key insight:
Even well-intentioned tools like sd and fastmod go through Bash and
would normally trigger approval prompts — but sd, rg, jq, yq, yamllint,
and fastmod are pre-approved in settings.json so they will not prompt.
The Edit tool never prompts regardless. Always prefer Edit first.

### When to use Edit tool vs sd:
- Single line replacement → sd is fine and pre-approved
- Multiline pattern (match spans a newline) → Edit tool only, sd will fail
- Uncertain whether pattern is single or multiline → Edit tool to be safe

### What Edit needs to work well:
- Read the file first to get the exact string before attempting an Edit
- Use rg to locate exact content if uncertain about surrounding text
- Make multiple small Edit calls rather than one complex Bash command
- Never construct a Bash command to handle uncertainty — Read first, then Edit

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
    client_ip.py            — The trust boundary: the ONE place the app decides who the caller is (TRUSTED_PROXY_IPS)
    rate_limit.py           — Per-identity fixed-window limits; named tiers, DB-backed counters, route dependencies
  routers/
    ingest.py               — POST /ingest
    synthesis.py            — POST /stream/synthesis (SSE); POST so a paid LLM call isn't reachable by cross-site navigation (ADR 0001). In-memory LRU cache keyed on the rendered prompt, so identical requests skip the LLM call
    search.py               — POST /search  (hybrid BM25 + pgvector)
    trends.py               — GET /trends
  prompts/synthesis_v1.yaml — Versioned system + user prompt template

/frontend/src
  api/client.ts             — All fetch calls, base URL from VITE_API_BASE_URL
  hooks/usePostStream.ts    — Shared POST+fetch SSE client: reader loop, frame parser,
                              quota/rate_limited refusal frames, abort. Both streams run on it
  hooks/useSSE.ts           — Synthesis stream (name predates the POST move)
  hooks/useChatStream.ts    — Chat stream; registers the `sources` frame handler
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

Tests live in `backend/tests/` (pytest + pytest-asyncio + pytest-cov, `asyncio_mode=auto` via `pytest.ini`; coverage prints on every run). They use a throwaway in-memory SQLite DB and mock the external boundaries (the Anthropic client, gkeepapi, the filesystem), so they never touch the real Postgres or the network. That is the one remaining SQLite in the repo and it holds no data — `conftest.py` sets a placeholder `DATABASE_URL` so the Postgres-only `config.py` imports, then swaps the engine. The pgvector store is Postgres-only, so `embed_and_store`/`search_similar` no-op off Postgres; real vector behavior is covered by `test_embeddings_integration.py`, gated on `TEST_DATABASE_URL`. The quota gate's cross-process serialization is likewise Postgres-only (`pg_advisory_xact_lock`) and covered by `test_quota_concurrency_pg.py` behind the same gate — the default SQLite suite cannot execute that statement, so **run the Postgres suites before touching `services/quota.py`**. Coverage is ~88%: the ingest dedup engine, date parsing, OCR helpers, the Alembic migration/drift guard (`test_migrations.py`), mood scoring, all five router endpoints (via `TestClient` with `get_session` overridden — see the `client`/`add_entry` fixtures in `conftest.py`), and the LLM/embeddings wrappers. Lowest-covered remainder: `main.py` lifespan and the iCloud `brctl` download path.

Database: **Postgres, and only Postgres** (ADR 0003). There is no SQLite fallback — `config.py` refuses to boot without a Postgres `DATABASE_URL`, because running on two engines produced two journals that silently diverged. `engine` and `get_session` live in `models/journal.py`. `scripts/dev.sh` sets `DATABASE_URL` in both modes; a local server is `docker compose up -d db` (`backend/docker-compose.yml`, `pgvector/pgvector:pg18-trixie` — Postgres 18 + pgvector). **Schema is managed by Alembic** (`backend/alembic/`) — `alembic upgrade head`, and the app also self-migrates on startup (`main._run_migrations`). One-time SQLite→Postgres data copy: `scripts/migrate_sqlite_to_pg.py` (preserves ids); embeddings are (re)built by `scripts/embed_backfill.py` into the pgvector `entry_embeddings` table. Deferred to the hardening phase: RLS + KMS envelope.

SSE format: each token is JSON-encoded (`json.dumps(token)`) so newlines survive the `data: …\n\n` SSE frame. The frontend `JSON.parse`s each event.

**Rate limiting** (`services/rate_limit.py`, decisions.md 2026-08-04): per-identity fixed-window counters in the DB (`rate_limit_counters`), incremented with one `INSERT … ON CONFLICT … RETURNING` — so unlike the quota gate it needs no lock. Applied as **route dependencies, not middleware** (middleware lands outside `CORSMiddleware` and its 429s lose the CORS header). Named tiers, all env-tunable (`RATE_LIMIT_*`); cheap reads including `/trends` (the Docker healthcheck) are exempt. The two SSE routes refuse with an `event: rate_limited` frame inside a 200, like the existing `event: quota` frame. Anonymous callers key on IP via `services/client_ip.py` — which is only trustworthy if **`TRUSTED_PROXY_IPS`** matches the deployment (the Dockerfile passes the same value to uvicorn's `--forwarded-allow-ips`; it is no longer `*`).

Mood scoring is a FastAPI `BackgroundTask` — it fires after ingest returns and calls `services/llm_client.py:structured_output`. Failures are silently swallowed.

**Demo**: `scripts/seed_demo.py` seeds ~65 crafted entries (all three sources, a deliberate mood arc) as a **`demo` user**. Demo-ness is **per-user** (`services/auth.py:is_demo_user`), not a global flag, so one instance serves the demo visitor and real users at once. Two ways to run it:
- **Signed-out demo of the one app (shape B, the hosting shape)**: seed into the shared Postgres (`DATABASE_URL=…pg… python scripts/seed_demo.py`), then run with `PUBLIC_DEMO=1` — a logged-out visitor resolves to the demo user (read-only: `/ingest` + `/keys` 403; chat/search open, bounded by `DEMO_DAILY_USD`), and signing in gives them their own journal. `seed_demo`'s wipe is **demo-user-scoped** (never touches other users). Semantic search works (pgvector embeddings seeded).
- **Whole-instance demo**: `APP_MODE=demo` resolves *every* request to the demo user and turns Keep sync off. It runs against the same Postgres — `APP_MODE` no longer selects a database, since there is only one.

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

Tests use Vitest + Testing Library (jsdom) — config lives in `vite.config.ts` (`test` block) with `src/test/setup.ts` for jest-dom matchers. `*.test.ts(x)` files are excluded from `tsc -b` (see `tsconfig.app.json`) so the production build stays test-free. Covered: `api/client.ts`, the `useSSE`/`useChatStream`/`useTrends` hooks (`fetch` + a `ReadableStream` body mocked), and the `DayDetail`/`StreamingPanel`/`ChatPanel` components. Untested so far: `MoodChart`, `SearchPanel`, the selectors, `App`.

## Key design decisions

- **Stop conditions** (check before doing): modifying `JournalEntry` schema, adding auth/user accounts, adding dependencies not in `requirements.txt` / `package.json`. (Schema changes now go through an Alembic revision — `alembic revision --autogenerate` — rather than the removed `ensure_schema` shim; `tests/test_migrations.py` guards model/migration drift.)
- **Ingestion stub**: accepts a plain `{"entries": ["…"], "source": "text"}` payload — Google Keep / GoodNotes API auth is not yet implemented.
- **Trends stub**: returns deterministic sinusoidal data when no scored entries exist, so `MoodChart` renders immediately.
- **Future**: multi-user, Postgres, GDPR-compliant isolation. The `source` field and content-hash dedup are designed to be idempotent across future import connectors.

## Skills

Matt Pocock's agent skills are installed under `.claude/skills/` (managed via `skills-lock.json`). Use `/skill-name` in Claude Code to invoke them (e.g. `/prototype`, `/tdd`, `/diagnose`).
