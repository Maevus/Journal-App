# Architecture decisions

## Multi-user architecture planned + Chatbot V2 shipped (2026-06-30)

- **Chatbot V2 shipped** — multi-turn conversation + structured clickable citations
  (`3c2687c`). `POST /stream/chat` carries the client-held conversation (stateless
  server); a cheap haiku `condense_query` rewrites follow-ups into standalone queries
  before the existing `parse_date_range` + `hybrid_search`. Answers cite entries inline
  with `[n]` markers; a terminal `event: sources` SSE frame carries the numbered entries
  so the frontend renders a clickable chip per *used* `[n]`, opening that entry's day in
  `DayDetail`. New `useChatStream` hook (POST + ReadableStream) sits alongside `useSSE`.
  See ADRs **0001** (chat over POST+fetch vs synthesis's GET+EventSource) and **0002**
  (citation protocol). A background review agent caught a real bug — an aborted/errored
  turn became an empty assistant message → API 400 on the follow-up — **fixed in
  `37328ec`** with the multi-turn tests that had been missing.
- **CONTEXT.md (root) is now the glossary; `docs/adr/` holds one-per-decision ADRs.**
  Adopted the grill-with-docs structure: `CONTEXT.md` for terms (turn/conversation/
  citation), `docs/adr/` for ADRs, this file stays the running log, `docs/plans/` for
  plans.
- **Multi-user architecture decided (planning only, nothing built)** — for a public
  launch (Reddit) + commercial practice. Six decisions in **ADR 0003** /
  `docs/plans/multi-user.md`: Sign in with Google (identity); httpOnly session cookie
  (sessions); `user_id` isolation re-applied per store (SQL + Chroma) via a
  `get_current_user` dependency + scoped data-access helpers + Postgres RLS + isolation
  tests, with per-user dedup as composite `UNIQUE(user_id, content_hash)`; **Postgres +
  Alembic** (replacing the SQLite-only `ensure_schema()`); a **freemium** cost model
  (metered free-tier on our key + global cap + haiku, BYO-key/paid upgrade — the per-user
  meter is also todo #13's usage widget); and the existing-data migration (owner user →
  nullable → backfill → NOT NULL, plus Chroma metadata backfill). The Postgres/DB choice
  is carved into its own supersedable ADR section. Build order: identity end-to-end first.

## Chatbot V1 shipped — RAG over hybrid search (2026-06-19)

- **Built per [`docs/plans/chatbot-v1.md`](plans/chatbot-v1.md)**: `GET /stream/chat?q=&limit`
  SSE, single-turn, inline date citations. Reuses the existing SSE pattern + `stream_text`.
- **Hybrid fusion extracted to `services/retrieval.py:hybrid_search`** (was inline in
  `routers/search.py`) so `/search` and `/chat` share one implementation; `/search`
  behaviour unchanged (its tests' Chroma stub repointed to `retrieval.search_similar`).
- **Chat ignores the global range/source filters and has no response cache** — a question
  defines its own scope, retrieval runs over **all** entries, each ask is one-off.
  `backend/prompts/chat_v1.yaml`: answer only from provided entries, cite dates inline, no
  clinical labels. Frontend: `ChatPanel` (full-width "Ask" panel) + `chatUrl`.
- **✅ FIXED — date-blind retrieval (2026-06-20).** Time-scoped questions ("energy levels in
  May/June") previously missed the 47 relevant May-2026 entries because `hybrid_search` matched
  `entry.content` only and `limit≈8` over ~962 entries crowded out date-relevant hits. Fix:
  `services/retrieval.py:parse_date_range` resolves a question's time scope via a cheap LLM
  `structured_output` call (haiku) — degrading to no-filter when not time-scoped or on failure —
  and `hybrid_search(start, end)` restricts the candidate set by `JournalEntry.timestamp` before
  fusion (widening the semantic pool when windowed). Chat falls back to unscoped retrieval when a
  window matches nothing, so it never falsely claims the journal is empty. Verified live on the
  real 962-entry DB: a May+June window returns 8/8 in-window hits including the previously-missed
  2026-05-11 Concerta entry.

## GoodNotes-from-Drive, export, chatbot plan (2026-06-19)

- **GoodNotes source is the local Google Drive mirror, not iCloud** — env var renamed
  `GOODNOTES_ICLOUD_PATH` → `GOODNOTES_PATH`. OCR skip is now **existence-based** (skip any
  already-ingested page; `force` to re-OCR), since the diary is one append-only growing PDF;
  optional APScheduler scan (`GOODNOTES_SCAN_INTERVAL_MIN`, default off). Full Google Drive
  **API/OAuth** (a future user connecting their own Drive) is deferred — it merges with the
  auth work ("Sign in with Google" = login + Drive in one).
- **CSV/JSON export** (`GET /export`) added — no-dep data export; rich PDF-with-charts deferred.
- **Chatbot designed, not yet built** — V1 = single-turn + inline date citations, RAG reusing
  the existing hybrid search + SSE streaming. Spec: `docs/plans/chatbot-v1.md`.
- **Isolated-worktree agents are the build workflow** — backend work runs in a `git worktree`
  with the shared `backend/.venv` symlinked (sandbox override for test runs); frontend stays
  in the main tree (no `node_modules` in a worktree). Proven on GoodNotes + export.

## Test suites: backend (pytest) + frontend (Vitest) (2026-06-18)

- **Backend testing = pytest + pytest-asyncio + pytest-cov** (`backend/pytest.ini`, `asyncio_mode=auto`, coverage on every run). Tests use an **in-memory SQLite DB** and **mock the external boundaries** (Chroma, AsyncAnthropic, gkeepapi, filesystem) so they never hit the network/real DB/Chroma. Run from `backend/` (absolute imports). ~88% coverage; the API tests use FastAPI `TestClient` with `get_session` overridden (see `client`/`add_entry` fixtures in `tests/conftest.py`) — these are integration tests through the full HTTP→router→DB→JSON path.
- **One real-Chroma integration test** (`tests/test_embeddings_integration.py`) covers the single seam the unit tests mock — real embed→search round-trip + ranking. Marked `integration`; deselect with `pytest -m "not integration"` for a fast unit-only loop. Decision: skip browser/e2e (Playwright) for now — a single-user app with strong types + API integration tests + this Chroma test gives enough confidence; e2e's flakiness/maintenance cost isn't justified here.
- **Frontend testing = Vitest + @testing-library/react (jsdom)** — config in `vite.config.ts` (`test` block), matchers via `src/test/setup.ts`. `*.test.*` files are **excluded from `tsc -b`** (`tsconfig.app.json`) so the production build stays test-free. External boundaries (`EventSource`, `fetch`, the SSE hook) are mocked.
- **`datetime.utcnow()` retired** in favour of `clock.utcnow_naive()` (`datetime.now(timezone.utc)` with tzinfo stripped) — preserves the app's **naive-UTC** storage convention so comparisons against stored timestamps stay valid. Don't reintroduce `datetime.utcnow()`.

## Mood reasoning stored per entry (2026-06-17)

- **`JournalEntry` schema unfrozen, deliberately** — added a `mood_reasoning` column. This is normally a stop condition ("schema is frozen — stop and ask"); the user explicitly approved it for this feature after being shown the implications. The scorer already generated a reasoning sentence (`_MoodScore.reasoning`) and discarded it; now it's persisted.
- **Idempotent SQLite migration via `ensure_schema()`** (`models/journal.py`) instead of a migration framework — `SQLModel.create_all` never ALTERs existing tables, so columns added later won't appear in a DB seeded earlier. `ensure_schema()` does `PRAGMA table_info` + `ALTER TABLE … ADD COLUMN` if missing; called in `main.py` lifespan after `create_all`. This is the pattern for future additive columns on SQLite (no Alembic).
- **Reasoning surfaced in DayDetail (click-through), not the chart tooltip** — mood-chart points are daily *averages* (multiple entries per day), so a per-point tooltip can't map to one reasoning. DayDetail already lists per-entry, so it's the natural home.
- **Backfill by re-scoring, not migration** — existing entries had no stored reasoning, so `scripts/backfill_mood_reasoning.py` re-scores any entry with `mood_reasoning IS NULL` (one LLM call each; resumable, per-entry commits). Demo gets canned score-band reasoning in the seeder (no LLM spend).

## Synthesis response caching + model refresh (2026-06-17)

- **In-memory synthesis cache keyed on the rendered prompt** (`routers/synthesis.py`) — identical requests reuse the generated text instead of spending another LLM call. The key is `sha256(version + model + max_tokens + system + user_prompt)`; since `user_prompt` embeds the selected entries, range, and source, any entry edit/add or param change produces a different key and regenerates automatically. Bounded LRU (`_CACHE_MAX=32`), resets on restart — no schema/migration/dep (clears the stop conditions). First-of-todo-list item; verified the 2nd identical request makes no LLM call.
- **Cache only on stream completion** — tokens accumulate during the stream and are stored only after it finishes, so a mid-stream failure caches nothing.
- **Model default → `claude-opus-4-8`** (was `claude-opus-4-5`) in `llm_client.py`, `synthesis_v1.yaml`, and the `synthesis.py` fallback (now imports `DEFAULT_MODEL` so it can't drift). **Cost constants corrected to $5/$25 per 1M** (were $15/$75 — overstating spend ~3×). Model + pricing will become per-user config once users bring their own key/model.

## Single APP_MODE switch for demo/real (2026-06-17)

- **One `APP_MODE` env var derives the whole data stack** (`backend/config.py`) — `demo` → `demo_journal.db` + `demo_chroma_db` + Keep sync off; `real` (default) → `journal.db` + `chroma_db` + sync on. Replaces having to set `DATABASE_URL` + `CHROMA_PERSIST_DIR` + `SYNC_ON_STARTUP` together, where forgetting `SYNC_ON_STARTUP=0` would pull real Keep notes into the demo DB. Supersedes the three-var recipe in the 2026-06-12 notes below.
- **`config.py` is the single source of truth** — `models/journal.py` and `services/embeddings.py` import `DATABASE_URL` / `CHROMA_PERSIST_DIR` from it instead of reading env directly; `main.py` reads `config.SYNC_ON_STARTUP`. Unknown `APP_MODE` values raise at import (typos fail loud rather than silently serving real data).
- **Individual vars still override** — explicit `DATABASE_URL` etc. win over the mode default, so any combination is reachable. **But `.env` must NOT pin those three** — `load_dotenv` makes them look explicit and silently neutralises `APP_MODE`. Removed them from `.env` (the real values were just the `APP_MODE=real` defaults anyway).
- **Startup banner via `print`, not `logger.info`** — the default uvicorn logging config swallows app-module `INFO`, so the "which DB am I serving" safety line is a plain `print` to stdout.

## Source filtering + demo data (2026-06-12)

- **Source filter lives at the analytics level, not search** — one App-level `source` state (like `range`) drives MoodChart, DayDetail, and synthesis via an optional `source` query param on `/trends`, `/entries`, `/stream/synthesis`. A search-panel filter was started and deliberately reverted.
- **Trends stub suppressed when a source filter is active** — a fake sinusoid for "GoodNotes only" would be misleading; an empty chart with a message is honest. Stub still appears for the unfiltered empty-DB case.
- **Synthesis staleness key includes source** — changing source (or range) flags the visible text stale rather than auto-regenerating, since synthesis costs an LLM call.
- **Demo data is a separate database, not rows in the real one** — `scripts/seed_demo.py` builds `demo_journal.db` + `demo_chroma_db/` (gitignored); the app is pointed at them via `DATABASE_URL` / `CHROMA_PERSIST_DIR`. Mood scores are hand-set (no LLM spend); timestamps are relative to seed time so demos stay fresh.
- **`SYNC_ON_STARTUP=0` required when serving the demo DB** — otherwise the startup Keep sync ingests real notes into it.

- Web app not mobile — capture handled by Keep/GoodNotes, dashboard is review-only
- Single user first, multi-user later
- SQLite for now, Postgres when going public
- SSE not WebSockets for LLM streaming
- Vite + React 19 + TS, FastAPI + Python 3.13

## Ingestion pipeline session (2026-05-22)

- **`GOOGLE_APP_MASTER_TOKEN` env var name** — gkeepapi token key is `GOOGLE_APP_MASTER_TOKEN` (not `GOOGLE_MASTER_TOKEN`); value was originally malformed (extra dict content from raw Python login response) — trimmed to bare `aas_et/…=` token
- **`GOODNOTES_ICLOUD_PATH` corrected** — was `~/Users/maeve/Library/…` (double path); fixed to `~/Library/Mobile Documents/…`
- **GoodNotes PDFs include OCR text layer** — `pypdf` extraction is sufficient; Claude vision fallback for PDFs removed. Vision retained only for raw `.png`/`.jpg`/`.jpeg` image files
- **`Keep.resume` → `Keep.authenticate`** — gkeepapi renamed this method in current version
- **iCloud Drive requires terminal FDA** — uvicorn must be launched from a terminal with Full Disk Access; Claude Code's process is insufficient. Dev workaround: `GOODNOTES_ICLOUD_PATH=~/Downloads`
- **`ingest_keep` uses `run_in_executor`** — gkeepapi is synchronous; wrapped to avoid blocking the event loop

## Scaffold session (2026-05-21)

- **Absolute imports from `backend/`** — backend is not a Python package; run `uvicorn main:app` from within `backend/`, not the repo root
- **SSE tokens JSON-encoded** — each token sent as `data: <json.dumps(token)>\n\n` so newlines survive the SSE frame; frontend `JSON.parse`s each event
- **Mood scoring as BackgroundTask** — LLM structured-output call fires after ingest returns; failures silently swallowed so ingest is never blocked
- **Hybrid search: 70% Chroma + 30% BM25** — semantic recall is primary; BM25 (rank_bm25) boosts exact-keyword matches
- **Trends stub data** — deterministic sinusoidal stub returned when no mood-scored entries exist, so MoodChart renders immediately without data
- **Tailwind v4 via @tailwindcss/vite** — no tailwind.config.js needed; configured entirely via `@import "tailwindcss"` in index.css
- **pyyaml added to requirements.txt** — needed to load prompts/synthesis_v1.yaml; implied by the versioned YAML prompt architecture
- **JournalEntry schema is frozen** — stop and ask before any modification; downstream implications for Chroma IDs, mood scoring, and future migration

## Custom skills location (2026-06-05)

- **Skills moved from `.agents/skills/` to `.claude/skills/`** — Claude Code only auto-discovers skills under `.claude/skills/`; `.agents/skills/` is a Matt Pocock convention but is not on the harness discovery path. Both directories coexist; `skills-lock.json` still tracks provenance.

## iCloud GoodNotes ingestion (2026-06-05)

- **`_ensure_local` handles evicted files** — iCloud evicts files to a `.{name}.icloud` placeholder; `brctl download` triggers pull, waits up to 30 s for the real file to appear
- **FDA still required on the terminal** — uvicorn must be launched from a terminal with Full Disk Access; `GOODNOTES_ICLOUD_PATH` can now point directly to the real iCloud path (`~/Library/Mobile Documents/com~apple~CloudDocs/GoodNotes 5 Auto Backup`)
- **Auto-backup must be enabled in GoodNotes** — GoodNotes → Settings → Auto-Backup → iCloud Drive, set format to PDF

## Handwritten note processing (OCR)

- GoodNotes exports may already include OCR text layer — check export format first
- If not: use GPT-4o vision in the ingestion pipeline to transcribe images
- Rejected: Tesseract (poor handwriting accuracy), Google Vision (extra service to manage)
## Multi-user isolation phase (2026-07-02)

- **Demo mode auto-logs-in a seeded demo user** — once data routes are gated,
  `APP_MODE=demo` resolves `get_current_user` to a seeded demo user instead of
  401ing: the showcase stays zero-friction, the isolation code path is identical
  (everything scoped to a real user_id), and no auth code is bypassed.
- **Logged-out real mode → login gate screen** — the dashboard renders only when
  authenticated; matches the data reality (every endpoint 401s) and avoids
  maintaining a broken empty-dashboard state.
- **`user_id` is nullable for now** — NULL marks legacy rows and the user-less
  startup-sync paths; scoped reads won't see them. Backfill + NOT NULL + FK
  tighten at the Postgres/Alembic migration, per ADR 0003.
- **Pre-existing SQLite DBs keep their global UNIQUE(content_hash)** — SQLite
  can't drop a baked-in column constraint via ALTER; harmless while those DBs
  are single-user. New DBs get the composite `(user_id, content_hash)`. The
  constraint swaps at the Postgres migration ("only the constraint swaps").
- **Reads funnel through one choke point** — `models/journal.py:user_entries(user_id)`
  is the only place `WHERE user_id = ?` is written; every read route (entries,
  trends, search, export, synthesis, chat) starts from it and is gated by
  `get_current_user`. `hybrid_search` takes a **required, keyword-only** `user_id`
  so a forgotten scope is a TypeError, not a cross-user leak — and a transposed
  call can't bind it to the adjacent int `limit`. Its SQL candidate set is the
  isolation boundary: out-of-scope semantic hits drop at fusion, so Chroma
  metadata filtering (next slice) is defense-in-depth, not load-bearing. The
  synthesis cache key includes `user_id` so cross-user non-leakage is a property
  of the key, not an accident of prompt contents.
- **The vector store is isolated too** — `embed_and_store` stamps `user_id` into
  Chroma metadata and `search_similar` filters the ANN query with
  `where={"user_id": …}` (both take the same required, keyword-only `user_id`).
  Defense in depth plus efficiency: the semantic candidate pool is spent on the
  user's own vectors instead of ranking everyone's. Unowned writes (`user_id=None`,
  the user-less startup sync) carry **no** user_id key at all — Chroma metadata
  can't hold NULL, and an absent key matches no filter, mirroring SQL's invisible
  NULL rows. `scripts/backfill_owner.py` stamps both stores from SQL ownership
  (SQL is the source of truth), so one idempotent command bridges old data.

## Freemium phase (2026-07-03)

- **Launch posture: the free tier costs Maeve $0** — the demo (with its synthesis
  cache) is the try-it experience; real accounts get local features free (search
  is ONNX + BM25, no API), and LLM features unlock with a BYO key. Flipping to a
  subsidized budget later is a config change, not a redesign. Provider-shaped but
  Anthropic-first: `model` stays a plain string, pricing stays data-driven, the
  key store gets a `provider` column — multi-provider is a later feature behind
  the same two llm_client functions, not a prerequisite.
- **Every LLM call persists one attributed `LLMUsage` row** — the meter lives in
  `llm_client._log_usage`, the choke point both LLM entry points already funnel
  through, so an unmetered call is structurally impossible (the spend-side twin
  of `user_entries`). `stream_text`/`structured_output` take the house-pattern
  required keyword-only `user_id` (explicit `None` = system work: metered for
  visibility, charged to nobody). Attribution rides the natural chain: routes
  bill the logged-in user, mood scoring bills the entry's owner, chat's
  condense/date-parse bill the asking user. The meter write never kills the call
  it's metering (tokens already delivered — log loudly, move on); quota (F2)
  fails closed instead, but runs *before* the money is spent.

## Freemium F2 — quota before the call (2026-07-14)

- **The quota gate lives in `llm_client`, beside the meter** —
  `services/quota.py:check_quota(*, user_id)` runs at the top of both LLM entry
  points, before any API traffic. Same argument as F1: the choke point makes a
  forgotten check structurally impossible, and it covers all five spend
  surfaces including background mood scoring and chat's nested haiku calls,
  which never pass through a route dependency. Free things stay free by
  construction: synthesis cache hits, search, trends, and export never reach
  `llm_client`, so they never reach the gate.
- **Semantics**: per-user `SUM(cost)` since UTC midnight (the two columns F1
  indexed for exactly this), refuse at spent **>= budget** — `>=` so the $0
  keyless budget refuses at zero spend. A **global daily cap** is checked
  first in the same gate: it applies to every call *including* `user_id=None`
  system work (it protects our key, not anyone's allowance), while system work
  is exempt from the per-user check. The check is retrospective (a call's cost
  is unknowable beforehand), so a user can overshoot by at most one call —
  the tail the global cap bounds.
- **Fails closed — the deliberate asymmetry with the meter**: a quota check
  that errors refuses the call (`QuotaExceededError`, same type callers already
  handle). The meter never kills the call it's metering because that money is
  already spent; here it isn't yet.
- **Refusal UX (user chose, 2026-07-13)**: both streams emit a named
  `event: quota` SSE frame inside the 200 response (EventSource can't read a
  4xx body; mirrors chat's `event: sources` precedent) — one wire format,
  defined once in `quota.sse_quota_frame`. Chat sends no sources frame after a
  refusal (no citation chips for an answer that never happened) and synthesis
  caches nothing. Frontend: `useSSE`/`useChatStream` expose `quota` state
  (named events need an explicit `addEventListener` — they bypass `onmessage`),
  panels render a gold `role="status"` notice, distinct from the error alert.
  Mood scoring logs a quota skip at INFO (expected behavior), not WARNING;
  condense/date-parse degrade as before (refusal costs $0; the turn still gets
  the quota frame at its main call).
- **Demo gets its own budget (user chose, 2026-07-13)** — demo mode logs every
  visitor in as the seeded demo user, so the $0 free tier would have refused
  the whole showcase (chat always, synthesis on any cache miss — a gap found
  in the recorded posture). `DEMO_DAILY_USD` (default $1/day) keeps the demo
  alive, bounded, and backstopped by the global cap. Budgets are env-tunable:
  `FREE_TIER_DAILY_USD=0`, `DEMO_DAILY_USD=1`, `GLOBAL_DAILY_USD=5` — the cap
  defaults to a real number because a kill-switch that defaults to off isn't
  one.
- **Tests opt into quota** — an autouse `quota_headroom` conftest fixture gives
  the suite a generous budget so the gate only bites where a test explicitly
  sets one (the meter-DB stance applied to money). Quota-math tests pin *both*
  knobs: the cross-user isolation test originally tripped the global cap by
  seeding $99 for the other user — refused for the right-wrong reason.

## Freemium F3 — per-user model resolution (2026-07-21)

- **Resolution is the third per-call step, beside quota and the meter** —
  `services/llm_client.py:resolve_model(*, user_id)` decides which model a
  generation call runs on, replacing the old "everyone gets Opus from the
  prompt YAML" default. It takes the same keyword-only `user_id` as
  `check_quota` and `_log_usage`, so the three policy steps (which model, may it
  spend, record the spend) share one shape and one choke point. The load-bearing
  invariant: a single `model` variable flows from resolution into both the API
  call and `_log_usage`, so what's billed is always what ran — the model string
  can't diverge between the call and the meter.
- **Free → `claude-haiku-4-5`, demo → `claude-opus-4-8`** — env-tunable via new
  `FREE_TIER_MODEL` / `DEMO_MODEL` config knobs, kept provider-shaped as plain
  strings (pricing stays data-driven in `_PRICING`) so per-user model config
  (F4, BYO keys) slots in without a redesign. In demo mode every visitor is the
  seeded demo user, so the mode picks the model; the demo showcase stays on
  Opus, bounded by `DEMO_DAILY_USD` + the global cap.
- **Task-fixed models bypass resolution** — OCR (Sonnet, needs vision),
  date-parse and condense (Haiku, trivial internal work) pass their model
  string explicitly, so they're honored as-is regardless of tier: a paying
  user's date-parse must not jump to Opus. Only user-facing generation
  (synthesis, chat, mood scoring) resolves per-user. The prompt-YAML `model:`
  field is now dead config and was removed from the three templates.
- **No owner, no generation (user chose, 2026-07-21)** — `resolve_model` raises
  for `user_id=None`. Mood scoring guards unowned rows (a connector sync before
  its account has logged in) with an explicit INFO skip rather than spending the
  app key on a model nobody owns; those rows get scored on the owner's budget
  once claimed (`scripts/backfill_mood_reasoning.py`). This makes the principle
  "mood scoring always bills to an owner's key" an enforced invariant, and fixes
  a posture inversion where anonymous rows were scored for free while a
  logged-in free-tier user was refused. System work that *does* run
  (explicit-model calls with `user_id=None`) still meters visibly, charged to no
  quota — it just has to name its model, since there's no owner to resolve one
  from.
- **Metering gap noticed, left for later** — `_ocr_with_claude` uses its own raw
  Anthropic client, so it bypasses `resolve_model`, `check_quota`, *and*
  `_log_usage` entirely. Pre-existing, unrelated to F3; worth a ticket.

## Freemium F4 — BYO keys, encrypted at rest (2026-07-21)

- **Fernet, chosen from `cryptography` (user chose, 2026-07-21)** — the de-facto
  Python standard: authenticated symmetric encryption (AES-128-CBC + HMAC), tiny
  API, well-audited. KMS envelope encryption was the more production-correct
  option but cloud-coupled and premature on local SQLite; the plan is to revisit
  it at the Postgres/deploy move. The Fernet key lives in `APP_ENCRYPTION_KEY`
  (env) and must be **stable** across restarts (unlike the random-per-boot
  `SESSION_SECRET`), or stored keys become undecryptable — so no random default:
  unset means the BYO feature is simply off, surfaced as a clear error only when
  a key is actually stored or read (`services/key_store.py`), never at startup.
- **The key store is a deep module** — `key_store` deals in plaintext keys at its
  interface (`store_user_key` takes one, `get_user_key` returns one); Fernet is
  entirely internal, so no caller handles ciphertext or a cipher object. New
  `user_api_keys` table, one row per (user, provider) with `provider` defaulting
  `"anthropic"` — a future OpenAI/Gemini key is a new row, not a schema change,
  the same provider-agnostic stance `LLMUsage.model` takes. New table only (no
  `JournalEntry` change), so `create_all` handles it; no Alembic yet.
- **The client runs on the user's own key** — `resolve_api_key(*, user_id)`
  returns the decrypted key or None (parallel to `resolve_model` /
  `check_quota`); `_async_client` applies the app-key env fallback in exactly one
  place, so None cleanly means "use the app key". A BYO key flows straight into
  client construction and is never logged.
- **Own-key calls bypass both caps, and BYO spend is excluded from the global
  sum (user chose query-time exclusion, 2026-07-21)** — a user paying their own
  key touches neither the free-tier budget nor the global cap (both protect *our*
  key), so `check_quota` exempts a key-holder before either check. Crucially, the
  global-cap sum also *excludes* key-holders' rows (`user_id NOT IN (SELECT … FROM
  user_api_keys)`, with `user_id IS NULL` system work always counted): otherwise
  one BYO user running Opus — easily past the $5 global cap in a day — would
  refuse every free-tier and system call. Chosen over an `on_app_key` column on
  `LLMUsage` to avoid a schema migration (a stop condition; formal migrations
  wait for Postgres per ADR 0003); under the $0 free-tier launch posture the
  query-time approach is exactly correct, since free users spend $0 on our key.
  BYO spend is still metered — it just doesn't gate anyone (the usage widget, F5,
  will show a user their own spend).
- **BYO users get the premium model** — `resolve_model` resolves a key-holder to
  `BYO_KEY_MODEL` (Opus) instead of free-tier Haiku: they pay their own way, so
  they get the best model. This is what makes an account first-class and lets the
  deferred mood backfill run at Opus quality on the owner's key.
- **Pre-call reads fail closed; the meter fails open** — `resolve_api_key`,
  `resolve_model`'s key check, and `check_quota` all read the DB *before* the
  call, so a DB error there refuses the call (no money spent yet), consistent
  with F2's quota stance. Only `_log_usage`, which runs *after*, swallows its own
  failures (the tokens are already paid for).
- **Key entry validates against Anthropic on save (user chose, 2026-07-21)** —
  `POST /keys` (`routers/keys.py`) validates a key with a real auth check before
  storing it, so a typo or revoked key fails *now* with a clear 400 rather than
  as a silent failure on the user's first synthesis. The check is
  `llm_client.key_is_valid` → `models.list()`, which authenticates the key but
  generates nothing, so validation costs no output tokens; only
  `AuthenticationError` means "bad key" (other errors propagate as "couldn't
  check right now", not a false rejection). The route also exposes `GET /keys`
  (a `has_key` boolean only — no key material) and `DELETE /keys` (revert to the
  free tier). Guards: 401 unauthenticated, 403 in demo (shared account), 503 if
  `APP_ENCRYPTION_KEY` is unset; the raw key is never echoed in any response.
- **Left for later** — a BYO generation call now does three separate key lookups
  (`resolve_model`, `check_quota`, `resolve_api_key`); correct but consolidatable.

## Freemium F5 — usage widget (2026-07-22)

- **`GET /usage` reads through `services/quota.py`, not the route session** —
  `usage_snapshot(*, user_id, days)` lives beside `check_quota`, sharing the
  extracted `_tier_budget()` / `_user_spend_since()` helpers and the module
  `engine` the gate itself reads. This makes widget–gate agreement structural:
  the two can't disagree because they run the same code on the same store. The
  deliberate cost is that `/usage` bypasses `get_session` dependency overrides —
  exactly as `check_quota` always has (tests point `quota.engine` at the meter
  DB via the `meter_db` fixture, and the F5 tests seed that DB, not the route's).
- **Uncapped is `null`, not a number** — a BYO key-holder gets
  `budget_usd: null` / `remaining_usd: null` on the wire: "no cap" is a
  different kind of answer than "$X left" (F4: own-key spend is metered for
  this widget but gates nobody). The frontend renders it as its own state.
- **`model` comes from `resolve_model`, not re-derived** — the widget shows the
  model the user's *next* generation actually runs on; duplicating the
  tier→model mapping in the router could drift from what generation does. One
  extra key lookup per request — same consolidatable-lookups loose end as F4.
- **`KEY_CHANGED_EVENT`** (`frontend/src/api/client.ts`) — saving/removing a
  BYO key dispatches a window event; `useUsage` refetches on it, so the tier
  chip and cap can't go stale while `ApiKeyPanel` sits beside the widget. Same
  one-emitter/one-listener pattern as `AUTH_EXPIRED_EVENT`, no shared state
  plumbing. Refetches deliberately don't re-show the skeleton (numbers swap in
  place) — which also keeps the new hook off the `setLoading`-in-effect lint
  error the older `useTrends` carries.
- **jsdom `ResizeObserver` stub added** (`src/test/setup.ts`) — recharts'
  `ResponsiveContainer` needs one; the stub lets chart-bearing components
  render in tests (asserting on text, not SVG). This also unblocks the
  previously-untestable `MoodChart`.
- **Postgres-migration note**: `usage_snapshot`'s daily grouping uses
  `func.date(created_at)` — a SQLite-ism to re-check alongside `ensure_schema`
  at the Postgres move (ADR 0003).

## Post-F5 loose-end sweep (2026-07-22)

The three gaps flagged in the F3/F4 handoffs, closed in one pass:

- **OCR now goes through the choke point** — `_ocr_with_claude` calls
  `llm_client.stream_text` (which already accepted full `messages`, so image/
  document blocks needed no llm_client change) instead of its own raw Anthropic
  client. That makes OCR quota-gated before it spends, metered after (Sonnet's
  real $3/$15 added to `_PRICING` — it had been silently falling back to Opus
  rates), and run on the owner's BYO key. Billing goes to `_resolve_sync_owner`
  — their pages, their budget; unowned sync is system work (global cap only).
  The model stays task-fixed (vision), per F3. A quota refusal mid-scan stops
  further OCR but **keeps and ingests the already-transcribed pages** — that
  money is spent; only future spend is refused.
- **One key read per generation call** — `_pre_call` in `llm_client` makes
  `resolve_api_key` the single "does this user have a key" read;
  `resolve_model` and `check_quota` accept its answer via an optional
  keyword-only `on_own_key` hint (None = self-serve lookup, so standalone
  callers like the synthesis router are unchanged). Key resolution now fails
  closed as `QuotaExceededError` — the F2 stance, and it turns an
  `APP_ENCRYPTION_KEY` misconfiguration into a clean refusal frame instead of
  a mid-stream 500. Threading the key from routers into the entry points was
  rejected: it would widen the plaintext-key surface F4 deliberately kept to
  key_store → client construction.
- **The frontend knows about demo mode** — `GET /auth/me` gained a `demo`
  boolean (`config.IS_DEMO`). `ApiKeyPanel` replaces the key form with a
  shared-account note (it previously offered a form whose save could only
  403), and `UsagePanel`'s tier chip reads "Demo" instead of the misleading
  "Free tier". Carried on the existing auth probe rather than a new
  `/config` endpoint — one fetch, and the flag is only meaningful logged in.

## Ask-agent mood access, chart date range, logging, demo-auth polish (2026-07-22 → 23)

- **The Ask agent now sees mood scores — which were never in Chroma.** Chroma
  holds only content embeddings + a `user_id` tag; the computed `mood_score`
  lives on `JournalEntry` (SQL), and `hybrid_search` already returns full rows,
  so the data was in hand — chat just dropped it. Two additions:
  (1) each retrieved entry's context block now carries its `0–1` mood (or
  `unscored`); (2) `retrieval.mood_summary()` aggregates overall + per-year
  averages over the user's scored entries and chat injects it, so average/trend
  questions — which RAG's ~8 retrieved entries can't answer — work off the full
  dataset. `chat_v2.yaml` explains the score is computed sentiment, distinct
  from any rating written in the entry text. **No SQL-injection surface**
  (Maeve asked): the summary is a fixed parameterized-ORM aggregation, the
  question never enters SQL; the only raw SQL in the repo is `ensure_schema`'s
  fixed DDL. `mood_summary` takes an optional `start`/`end` window (the same one
  chat parses for retrieval) so a time-scoped mood question summarises that
  period; unscoped stays whole-journal.
- **Custom date range on the mood chart** — `GET /trends` accepts end-inclusive
  `start`/`end` dates that override the `days` lookback (the sinusoid stub is
  suppressed for a custom window, as for a source filter). `MoodChart` gained
  From/To pickers; picking a preset clears the window via a **remount `key`**
  (`key={range.days}` in App) rather than a `useEffect` that clears state —
  keeps the frontend off the `set-state-in-effect` lint rule. First-ever
  MoodChart component test added.
- **Root logging configured (`main._configure_logging`, in the lifespan)** —
  uvicorn configures only its `uvicorn.*` loggers and leaves the root logger
  handlerless, so every app-module `logger.info` fell to the WARNING lastResort
  and vanished (21 call sites; the startup banner had to be a raw `print`). One
  INFO StreamHandler on root fixes it; it lives in the lifespan so the
  TestClient (which skips lifespan) leaves pytest's capture untouched. This is
  what surfaced the login failure's actual cause.
- **Login `invalid_client` was a stale-secret typo, not code** — a trailing `2`
  on `GOOGLE_OAUTH_CLIENT_SECRET` in `.env` (36 chars vs the real 35). OAuth
  login (Web-client `GOOGLE_OAUTH_CLIENT_ID/SECRET`) and Keep import (gkeepapi
  `GOOGLE_APP_MASTER_TOKEN`) are fully independent — Keep synced 921 notes fine
  throughout. `SESSION_SECRET` is now pinned in `.env` (was random-per-boot) so
  a login survives a backend restart mid-handshake; it only signs the transient
  `oauth_state` cookie, not the real session (a DB token).
- **Demo mode hides real auth controls** — `AuthWidget` shows a "Demo mode"
  badge with no sign-out in demo (sign-out nulled the user → dropped onto the
  LoginGate → whose Google login can only error/be ignored, since
  `get_current_user` hard-resolves to the demo user under `IS_DEMO`).
- **Usage + API key are one tabbed card (`AccountPanel`)** — the sticker labels
  double as tabs; opens on the API-key tab only when a signed-in non-demo user
  has no key yet, else Usage. Replaces two half-empty side-by-side cards.

## SQLite → Postgres migration (2026-07-23)

The deferred engine move from ADR 0003, sequenced *before* security hardening
(Maeve's call: harden the DB we'll actually ship, and RLS/KMS reshape that same
surface). Scope was deliberately "engine move only" — **RLS + the KMS envelope
are deferred to hardening**, and choosing the managed prod host + CI stays with
deploy. Target is local Docker Postgres with a portable `DATABASE_URL`.

- **Postgres 18, local via Docker** (`backend/docker-compose.yml`). Pinned the
  major tag (`postgres:18`, current stable — 16 was two majors behind). PG 18
  moved the container VOLUME to `/var/lib/postgresql` (PGDATA now in a version
  subdir); mounting the old `…/data` would silently not persist.
- **Alembic replaces `ensure_schema()`.** `env.py` reads the URL from
  `config.DATABASE_URL` (the app's one knob) with an override hook for tests; the
  app self-migrates on startup (`main._run_migrations`, best-effort/logged, so a
  stray legacy DB warns instead of failing boot). The initial migration was
  autogenerated; `import sqlmodel` added to it and to `script.py.mako` (the known
  SQLModel-autogen gap). `tests/test_migrations.py` guards model/migration drift
  (SQLite) and round-trips upgrade/downgrade + the composite unique on Postgres
  when `TEST_DATABASE_URL` is set.
- **`JournalEntry.user_id` tightened to NOT NULL.** The consequence the ADR
  glossed: the connector startup sync used to insert unowned (NULL) rows to be
  backfilled later. Under NOT NULL that would crash, so `ingest_entries` now
  **skips** when no owner resolves (a sync whose account hasn't logged in ingests
  nothing and defers) rather than orphaning rows — a stronger invariant. The
  "unowned rows are invisible" isolation test is gone (that state can't exist);
  `LLMUsage.user_id` **stays nullable** (system work is legitimately ownerless).
- **Data copy** (`scripts/migrate_sqlite_to_pg.py`): run `backfill_owner.py`
  first (no NULL owners), then copy every table FK-safe **preserving primary
  keys** so Chroma vectors (keyed by `str(entry.id)`) stay aligned with no
  re-embed; resets Postgres sequences after the explicit-id inserts; the source
  `journal.db` is read-only, i.e. an intact rollback.
- **Query portability**: the only SQL date-bucketing was `func.date()` in
  `quota.py` (Postgres has no plain `date()` scalar) — now a dialect-aware
  `_day_bucket` (cast to DATE on PG). `trends`/`mood_summary` already bucketed in
  Python, so they were already portable (the handoff's worry there was a false
  alarm). Engine `connect_args` branch on the URL scheme.
- **Demo** (SQLite, built by `create_all`) now `alembic stamp head`s after
  seeding so the startup upgrade is a clean no-op instead of re-CREATEing tables.

## Chroma → pgvector (2026-07-24)

Moved the vector store out of Chroma (a local `chroma_db/` directory — the
backend's only stateful-filesystem dependency) into Postgres via **pgvector**.
Prompted by hosting: it makes the backend storage-stateless (scale-to-zero
container hosts like Cloud Run, horizontal scale on a shared store) and gives the
vectors a `user_id` column ready for RLS. Fits the confirmed shape — **one
deployment**, the demo as a signed-out seed user in the same Postgres.

- **Clean 2-function interface saved the day.** The store is behind
  `embed_and_store` + `search_similar`; preserving both signatures meant
  `retrieval.hybrid_search` (does `dict(search_similar(...))`), `ingestion`, and
  the search/retrieval/isolation tests (mock at that boundary) needed **no change**.
- **Embeddings**: `fastembed` (ONNX, no torch) with `all-MiniLM-L6-v2` (384-d,
  the model Chroma's default used) — *lighter* than chromadb, which is dropped.
  All 980 entries **re-embedded** locally (`scripts/embed_backfill.py`, ~37s, free)
  rather than porting Chroma's vectors — simpler, one consistent space. Query
  ranking checks out: "climbing" still returns entry 690 top.
- **Storage**: a **Postgres-only `entry_embeddings`** table (`entry_id` PK/FK
  cascade, `user_id` NOT NULL, `embedding vector(384)`, HNSW cosine index),
  created by a **hand-written** Alembic migration (autogenerate emits neither the
  extension nor HNSW) and kept **out of `SQLModel.metadata`** so SQLite never
  tries to build a `vector` column. `embeddings.py` talks to it via parameterized
  SQL (bind the vector as `'[…]'::vector` — no per-connection type registration).
- **Graceful degrade off-Postgres**: `search_similar` → `[]`, `embed_and_store`
  → no-op. Keeps the SQLite unit suite green and the interim SQLite demo running
  (its semantic search is dormant until the demo moves onto Postgres — the agreed
  follow-up). Real vector behavior is `TEST_DATABASE_URL`-gated
  (`test_embeddings_integration.py`); the drift test is unaffected (table not in
  metadata).
- **Docker image**: `postgres:18` → **`pgvector/pgvector:pg18-trixie`**. The
  `-trixie` variant is deliberate — the plain tag is bookworm (glibc 2.36) and the
  data volume was initialised on trixie (glibc 2.41); mixing glibc collation
  versions on one volume warns and risks collation-sensitive index corruption.
  Supabase ships pgvector, so prod needs no image change.

## Demo as the signed-out state of the one app (2026-07-24)

The hosting shape: **one deployment** where a visitor who lands without an account
sees a read-only demo, and signing in gives them their own journal — same app,
same Postgres. So demo-ness moved from a **global switch** (`APP_MODE=demo`
resolving *every* request to the demo user) to a **per-user fact**, because one
instance now serves the demo visitor and real logged-in users simultaneously.

- **Per-user, not global.** `services/auth.py:is_demo_user(user)` (anchored on the
  stable `google_sub == "demo"`) + a lazily-cached `is_demo_user_id(user_id)`
  replace the ~5 `config.IS_DEMO` checks (quota budget, model resolution, `/keys`
  403, `/auth/me`, and the new `/ingest` guard). The cache is a per-process
  constant in prod; tests reset it (a `conftest` autouse fixture) since each uses
  a fresh DB.
- **`PUBLIC_DEMO` flag.** `get_current_user` now resolves: valid session → the real
  user (a login always wins); else demo user when `IS_DEMO or PUBLIC_DEMO`; else
  401. Off (default) keeps the private instance's logged-out → 401. `APP_MODE=demo`
  is retained (a standalone SQLite demo — everyone is the demo user), and the
  per-user checks are simply true for everyone there, so nothing regresses.
- **Read-only demo.** The demo visitor can chat + search (POST-but-read, bounded by
  `DEMO_DAILY_USD`) but `/ingest` 403s — one visitor can't pollute the shared demo
  journal the next one sees. `/keys` already 403s.
- **Seed into the shared Postgres.** `seed_demo.py` seeds the demo user + entries +
  **pgvector embeddings** (so demo search works) into `DATABASE_URL` (Postgres),
  falling back to the SQLite demo db only when no `DATABASE_URL` is set — never the
  real `journal.db` by accident. Its wipe is now **demo-user-scoped** (delete only
  that user's rows; the old whole-file unlink is unsafe in a shared DB). Verified:
  seeding + re-seeding leaves Maeve's 980 entries + embeddings untouched.
- **Frontend.** `App` already renders for any non-null user, so under `PUBLIC_DEMO`
  a logged-out visitor lands *in* the demo (no `LoginGate`). Added a `DemoBanner`
  (shown when `user.demo`) — "you're exploring a sample journal, sign in to start
  your own" — the conversion CTA. `LoginGate` stays for the private-instance 401.

## Branch review + deploy readiness (2026-07-29)

Reviewed the whole outstanding stack commit-by-commit and landed it on `main`, then
fixed what blocks a real deploy. The stack was linear (datepicker ⊂ postgres-migration
⊂ pgvector-switch ⊂ demo-signed-out), so review was scoped **per commit, not per
branch** — reviewing per branch would have re-reviewed the datepicker four times.

- **Land first, gate on `PUBLIC_DEMO`, not on the merge.** Nothing found was a
  cross-tenant leak, and `PUBLIC_DEMO` defaults off, so `main` exposes nothing on its
  own. The findings became a gated fix queue rather than a merge blocker.
- **Metering moved into a `finally` (`llm_client.stream_text`).** A client that hangs
  up mid-stream unwinds the generator at `yield`, so `get_final_message()` never ran
  and the call recorded $0 while Anthropic still billed it — `DEMO_DAILY_USD` and
  `GLOBAL_DAILY_USD` were both bypassable by reading one frame and disconnecting, on
  loop. The partial estimate (~4 chars/token) deliberately rounds **up**: over-charging
  an abandoned call is the safe direction.
- **Read-only demo is now a dependency, not a convention.** `require_writable_user`
  replaces hand-placed `is_demo_user` checks on every mutating route. The old pattern
  had already failed once — `DELETE /keys` was missed, so a signed-out visitor could
  clear the demo account's stored key. A parametrized test pins the write surface.
- **Migrations: loud, and a deploy step in production.** A failed `alembic upgrade` now
  aborts boot instead of being swallowed — the old behaviour booted clean on an empty
  schema and 500'd every request while passing a port-based health check. `AUTO_MIGRATE`
  defaults on (dev stays a one-liner) and the Dockerfile sets `0`, satisfying the
  security checklist's §7 where it matters without breaking `dev.sh`.
- **Cookie `Secure` is config, not `request.url.scheme`.** Behind a TLS-terminating
  proxy the app sees plain http and would ship the session token unprotected.
- **The image bakes the embedding model in** (`backend/Dockerfile`,
  `FASTEMBED_CACHE_DIR=/opt/fastembed`). fastembed otherwise caches to the system temp
  dir, so the "no stateful filesystem dependency" claim that motivated the Chroma
  removal was not actually true — a scale-to-zero cold start pulled ~90 MB from
  HuggingFace synchronously inside the first request. Verified with `--network none`.
- **Postgres-backed tests refuse a non-`_test` database.** They run `alembic downgrade
  base`; the skip message pointed at "a pgvector Postgres", and the only one documented
  was the working database. Following the instructions destroyed it.
- **Dev Postgres binds to loopback.** `"5432:5432"` published on `0.0.0.0` with
  `journal`/`journal`, and that role is the cluster superuser.

Known and accepted for now: the quota gate is still read-then-act (20 concurrent
requests passed a $1 budget and spent $11), and there is no rate limiting anywhere.
`GLOBAL_DAILY_USD` is the only hard stop. Both must be fixed before `PUBLIC_DEMO=1`.

## Synthesis over POST, and one streaming client (2026-08-04)

- **`GET /stream/synthesis` was CSRF-reachable, and it spends money.** The session
  cookie is `SameSite=Lax`, which the browser sends on a top-level cross-site GET,
  and CORS only stops a third party *reading* a response — it never stops the
  server *processing* the request. So any page could point a link or an iframe at
  the endpoint and burn a logged-in user's daily budget; a BYO-key user, who is
  quota-exempt by design (F4), would have been spending their own Anthropic key,
  and neither would ever see it happen. Now `POST /stream/synthesis` with the
  scope (`limit`/`days`/`source`) as a JSON body: that is not a CORS "simple
  request", so the browser must preflight, and the `FRONTEND_URL` allow-list
  already refuses the preflight. The scope moved into the *body* deliberately —
  a form-encoded POST would still be simple, and simple means no preflight. A CSRF
  token was rejected: a second secret to mint, store and rotate for a guarantee
  the method boundary already gives. A test pins that GET now 405s.
- **The two streaming transports converged instead of diverging.** ADR 0001's
  "synthesis stays GET+EventSource" half is superseded there. `useChatStream`
  already had a complete POST+fetch SSE client hidden inside it; the reader loop
  and frame parser are now `hooks/usePostStream.ts`, taking a per-event handler
  map, and both hooks are thin shells on it — chat registers a `sources` handler,
  synthesis registers none. The refusal frames are protocol rather than per-stream
  payload, so the shared client owns them for everyone.
- **Free from the transport change**: `reportIfAuthExpired` on the synthesis
  stream (a mid-session 401 now flips to the login gate instead of reading as
  "Stream connection failed" — `EventSource` exposes no status code, so this was
  previously impossible), and "Stop generating" aborting on the wire rather than
  just closing the reader. The one loss, `EventSource`'s auto-reconnect, cost
  nothing: `useSSE` already closed the connection in `onerror`.
- **`event: rate_limited` handled ahead of the limiter that emits it** — same
  shape as the quota frame (`{code, message, retry_after}` inside a 200 stream),
  built into the shared client against the agreed wire contract so the frontend
  is ready when the backend rate limiting lands. Rendered as a gold
  `role="status"` notice like quota, never the red error banner: being asked to
  slow down is an answer, not a failure.
- **Both panels now render the server's refusal message.** They had been
  hardcoding their own copy and ignoring `quota.message` — including a stale
  "Bring-your-own-key support is coming soon" line, months after BYO shipped.
  The backend knows the actual budget, tier and retry window; the panel doesn't.
- **`useSSE` keeps its name** though it is no longer EventSource-specific and both
  streams are SSE now. Renaming it to `useSynthesisStream` (the honest pairing
  with `useChatStream`) is a clean follow-up, deliberately not bundled into a
  security fix.
