# Journal Analytics

A journal analytics app. It ingests entries from Google Keep, handwritten
GoodNotes pages, and plain text; scores their mood; and lets the owner search,
chart, synthesise, and chat over their own writing — with every answer cited back
to the entries it came from.

Built single-user, then rebuilt multi-user with per-account isolation, a spend
quota, and BYO API keys.

![Dashboard](docs/demo.png)

## What it does

- **Ingest** — Google Keep sync, a watched GoodNotes folder (exported PDFs, OCR'd
  page by page with Claude vision), and plain text. Content-hash dedup per source,
  so every connector is idempotent and re-running one is free.
- **Mood scoring** — each entry is scored 0–1 with a one-sentence rationale, as a
  background task after ingest.
- **Trends** — a 30-day mood chart; click a day to read what was behind it.
- **Hybrid search** — BM25 lexical + pgvector semantic over a local MiniLM-384
  embedding, fused into one ranking.
- **Synthesis** — a streaming narrative summary over a chosen range and source.
- **Chat** — grounded Q&A over the journal. Answers stream with inline `[n]`
  markers; a terminal `sources` frame carries the retrieved set, and only the
  markers the answer actually used become clickable chips.

## Stack

**Backend** — FastAPI, SQLModel, Postgres 18 + pgvector, Alembic, fastembed
(ONNX, local), the Anthropic API.
**Frontend** — React + TypeScript (strict), Vite, Tailwind v4, Recharts, zustand.
**Deploy** — one container serving the API and the built SPA; self-migrating on
boot, or `alembic upgrade head` as an explicit deploy step.

## Architecture

```
/backend
  models/journal.py       JournalEntry + engine + get_session.
                          user_entries(user_id) is the isolation choke point —
                          `WHERE user_id = ?` is written exactly once.
  services/
    llm_client.py         Anthropic wrapper: streaming, structured output, retry,
                          per-call cost logging, per-user model + key resolution
    quota.py              Spend gate. Reserves before the call, not after, so
                          concurrent requests can't both pass a stale total
    rate_limit.py         Per-identity fixed-window counters, as route
                          dependencies rather than middleware
    client_ip.py          The trust boundary: the one place the app decides who
                          the caller is
    embeddings.py         pgvector store: embed_and_store, search_similar
    retrieval.py          Hybrid BM25 + ANN fusion, scoped per user
    ingestion.py          Hash-deduplicated ingest + background mood scoring
  routers/                ingest, search, trends, synthesis, chat, keys, usage, auth
/frontend/src
  hooks/usePostStream.ts  One POST+SSE client: reader loop, frame parser, refusal
                          frames, abort. Both streams run on it
  components/             StreamingPanel, ChatPanel, MoodChart, SearchPanel, DayDetail
```
## Future goals

Reshape it to a local-first app with data processing carried out in the browser (or similar), so that user data is never unencrypted in the cloud to make it appealing to users.

Write sensitive data to users own storage. 
Browser / multiplatform / sync? 
Tauri / electron. 


## Running it

Postgres first — the app will not start without one:

```bash
cd backend && docker compose up -d db
```

Backend:

```bash
cd backend
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # add ANTHROPIC_API_KEY
.venv/bin/uvicorn main:app --reload
```

Frontend:

```bash
cd frontend
npm install
cp .env.example .env
npm run dev                   # :5173
```

Imports are absolute from `backend/`, so run `uvicorn` and `pytest` from inside it.
`scripts/dev.sh {demo|real}` starts both servers together and is safe to re-run;
`scripts/dev.sh stop` kills them.

`.env.example` documents every setting, and ends with a production deploy manifest
separating what must be set from what should be reviewed.

### Demo mode

`PUBLIC_DEMO=1` lets a signed-out visitor land in a seeded read-only demo account
while real users still sign in to their own journals — demo-ness is per-user, not a
global flag. Seed it with `backend/scripts/seed_demo.py`.

## Tests

```bash
cd backend && .venv/bin/pytest    # 333 passing, 90% coverage
cd frontend && npm test           # 111 passing
```

The backend suite runs on in-memory SQLite with the external boundaries mocked, so
it never touches Postgres or the network. The parts that are genuinely
Postgres-only — pgvector search and the advisory-lock quota path — have their own
suites gated on `TEST_DATABASE_URL`. `test_migrations.py` fails if the models and
the Alembic revisions drift apart.

## Status

A personal project, built for my own journal and still primarily used for it.
[`security-checklist.md`](security-checklist.md) is the pre-launch threat model it
is being hardened against — written before deploying anywhere public, and worked
through rather than assumed.
