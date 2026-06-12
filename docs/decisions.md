# Architecture decisions

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