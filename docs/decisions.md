# Architecture decisions

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