# Multi-user architecture

The app was built single-user ("one person, all the data is theirs"). We're making
it multi-user to deploy publicly (shared via Reddit etc.) and as practice for
commercial work. This records the cohesive set of decisions; the full reasoning and
the teaching detail live in [`docs/plans/multi-user.md`](../plans/multi-user.md).

Multi-user is "whose is this?" applied everywhere, in three layers: **identity**
(login), **isolation** (you only see your own data), and **per-user config** (your
key/model). The decisions below build that, and several clear the stop-condition bar
(`JournalEntry` schema change, new deps, the SQLite→Postgres move).

## Identity — Sign in with Google (OAuth)

Delegate identity to Google rather than storing passwords: least code, least risk
(no password hashes to protect), and login + the GoodNotes/Drive import are the same
OAuth consent. Anchor accounts on Google's stable `sub` claim, never the mutable
email. Mint our **own** `users.id` PK that all journal data foreign-keys to (Google's
`sub` is one column) — so adding another login method later touches one table.

## Sessions — httpOnly session cookie

After the one-time Google login, a server-side session keyed by a random id in an
`httpOnly` cookie proves "still me" on each request. `httpOnly` keeps it unreadable
to JavaScript (an XSS bug can't steal the login — unlike a JWT in `localStorage`),
and revocation is just deleting the session row. Cookie flags `httpOnly`, `Secure`,
`SameSite=Lax` (the latter mitigates CSRF, since cookies are auto-sent). JWT's
stateless-horizontal-scale advantage is a problem we don't have.

## Isolation — defense in depth, every store

`JournalEntry` (and all journal data) gains a `user_id` FK → `users.id`. Principle:
every read filters by the current user, every write stamps them — and *isolation is
only as strong as the single weakest query*, so the goal is making the filter hard to
forget, not relying on memory:

- **`get_current_user` dependency** — resolves the session cookie → `User` (also the
  auth gate, 401 if absent). Provides the user but doesn't force the filter, so:
- **User-scoped data-access helpers** — entry reads funnel through helpers that
  always apply `WHERE user_id`; the choke point.
- **Re-applied per store** — the vector store (Chroma) isn't SQL, so `WHERE user_id`
  doesn't touch it: stamp `user_id` into each vector's metadata and filter every
  `search_similar`. Content-hash dedup becomes per-user via a composite
  `UNIQUE(user_id, content_hash)` (keeps the hash a pure content fingerprint and
  `user_id` an orthogonal scoping dimension).
- **Isolation tests** — actively attack the boundary (user B requests user A's id →
  empty/403) so a forgotten filter fails CI.
- **Postgres Row-Level Security** — DB-enforced backstop (see DB section).

## Database — Postgres (independently supersedable)

> This section is the one decision with a known independent future (a managed-host
> change, or revisiting the engine). If that changes, supersede *this section* with a
> follow-up ADR rather than disturbing the rest.
>
> **Update (2026-07-23): the engine move landed.** Freemium (F1–F5) shipped on
> SQLite first (a deliberate deviation from "Postgres now"); this is the deferred
> catch-up. Alembic replaced `ensure_schema()`; `JournalEntry.user_id` is now NOT
> NULL (ownerless connector syncs skip rather than orphan); local dev runs
> `postgres:18` via `backend/docker-compose.yml`; `scripts/migrate_sqlite_to_pg.py`
> copies the real data preserving ids (Chroma stays aligned). **RLS and the KMS
> envelope below are deferred to the hardening phase** — app-layer isolation
> (`user_entries` + Chroma metadata filter) remains the enforced boundary meanwhile.
> The managed prod host + CI/CD stay with the deploy work.

Postgres, now — not "SQLite then migrate." A public launch means concurrent
strangers (SQLite's single-writer lock stalls under load), deployed apps run multiple
instances that can't share a SQLite file, and journals belonging to the public make
the **RLS** backstop genuinely worth it (even a forgotten `WHERE` can't leak). RLS
needs the app to set the current user on each connection (`SET app.current_user_id`)
so policies can enforce it. Adopt **Alembic** for migrations — the hand-rolled
`ensure_schema()` (PRAGMA + ALTER) was a deliberate SQLite-only choice; Postgres + an
evolving schema needs a real migration tool (new dependency, accepted).

## Cost model — freemium

A public launch bills a stranger's question to *our* global key — a real
surprise-bill risk. Freemium over BYO-key-only because BYO-key's friction is at the
front door (no Anthropic key → bounce); free tier on our key, BYO-key/paid as the
upgrade. Minimum safe mechanism: a **persisted per-user usage meter** (extends
`_log_usage`; also the usage widget), a **quota check before the LLM call** (over
limit → refuse, no call), a **global daily cap** backstop, a **cheap free-tier model**
(`claude-haiku-4-5`; the per-user model config the `llm_client` comment anticipated),
and **encrypted-at-rest storage** for BYO/paid keys (never logged or returned).
Metering needs identity, so "try it" = log in with Google first (no anonymous usage).

## Migration of existing data

The ~962 existing entries predate `user_id`; you can't add a `NOT NULL` column to a
populated table directly. Canonical Alembic data migration: create the owner user →
add `user_id` nullable → backfill `user_id = owner` → tighten to `NOT NULL` + FK +
the composite unique + RLS policies → backfill `user_id` into existing Chroma vector
metadata → wrap in the one-time SQLite→Postgres data load. Choosing the composite
`(user_id, content_hash)` means existing hashes need no recompute — only the
constraint swaps.

## Consequences

- Stop conditions intentionally crossed: `JournalEntry` schema change; new deps
  (auth/OAuth, psycopg, Alembic, an encryption lib); the SQLite→Postgres move.
- Isolation correctness is now safety-critical (it's people's journals) — app-layer
  scoping + RLS + isolation tests all carry weight.
- Build order (recommended): identity (users + Google login + session cookie) first,
  end-to-end, *before* any data-model change.
