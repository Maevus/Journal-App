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

## Design decisions worth reading

The reasoning is written down as it happened. [`docs/decisions.md`](docs/decisions.md)
is the running log; [`docs/adr/`](docs/adr/) holds the three decisions big enough to
need their own document. A few that were more interesting than they look:

- **[Postgres is the only database](docs/decisions.md#postgres-is-the-only-database-2026-08-05)** — there used to be a SQLite
  fallback. Running on two engines produced two journals that silently diverged, so
  the app now refuses to boot without a Postgres URL. No fallback is a feature.
- **[Quota before the call, with a reservation](docs/decisions.md#quota-reservation--closing-the-read-then-act-race-2026-08-04)** — checking
  spend then spending is read-then-act: two concurrent requests both read the old
  total and both pass. The gate reserves under `pg_advisory_xact_lock` instead.
- **[Rate limits as route dependencies, not middleware](docs/decisions.md#per-identity-rate-limiting-2026-08-04)** —
  middleware lands outside `CORSMiddleware`, so its 429s arrive without the CORS
  header and the browser reports a network error instead of a rate limit.
- **[ANN recall under a per-user filter](docs/decisions.md#ann-recall-under-a-per-user-filter-2026-08-04)** — a filtered HNSW
  search silently loses recall as the table grows unless `hnsw.iterative_scan` is
  on, which needs pgvector 0.8+. Failure mode: fewer results, no error.
- **[ADR 0001 — both LLM streams over POST+fetch](docs/adr/0001-chat-streams-over-post-fetch.md)**
  — `EventSource` is GET-only, which left a paid LLM call reachable by cross-site
  navigation.
- **[ADR 0002 — citation protocol](docs/adr/0002-citation-protocol.md)** — how
  inline `[n]` markers and the terminal `sources` frame stay in agreement.
- **[ADR 0003 — multi-user architecture](docs/adr/0003-multi-user-architecture.md)**
  — the isolation model, the freemium tiers, and BYO keys encrypted at rest.

[`CONTEXT.md`](CONTEXT.md) pins the domain vocabulary — what counts as an *Entry*
versus a *Turn* versus a *Citation*, and which near-synonyms were deliberately
rejected.

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
