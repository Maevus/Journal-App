# Architecture decisions

- Web app not mobile — capture handled by Keep/GoodNotes, dashboard is review-only
- Single user first, multi-user later
- SQLite for now, Postgres when going public
- SSE not WebSockets for LLM streaming
- Vite + React 19 + TS, FastAPI + Python 3.13