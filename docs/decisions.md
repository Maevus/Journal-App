# Architecture decisions

- Web app not mobile — capture handled by Keep/GoodNotes, dashboard is review-only
- Single user first, multi-user later
- SQLite for now, Postgres when going public
- SSE not WebSockets for LLM streaming
- Vite + React 19 + TS, FastAPI + Python 3.13

## Scaffold session (2026-05-21)

- **Absolute imports from `backend/`** — backend is not a Python package; run `uvicorn main:app` from within `backend/`, not the repo root
- **SSE tokens JSON-encoded** — each token sent as `data: <json.dumps(token)>\n\n` so newlines survive the SSE frame; frontend `JSON.parse`s each event
- **Mood scoring as BackgroundTask** — LLM structured-output call fires after ingest returns; failures silently swallowed so ingest is never blocked
- **Hybrid search: 70% Chroma + 30% BM25** — semantic recall is primary; BM25 (rank_bm25) boosts exact-keyword matches
- **Trends stub data** — deterministic sinusoidal stub returned when no mood-scored entries exist, so MoodChart renders immediately without data
- **Tailwind v4 via @tailwindcss/vite** — no tailwind.config.js needed; configured entirely via `@import "tailwindcss"` in index.css
- **pyyaml added to requirements.txt** — needed to load prompts/synthesis_v1.yaml; implied by the versioned YAML prompt architecture
- **JournalEntry schema is frozen** — stop and ask before any modification; downstream implications for Chroma IDs, mood scoring, and future migration