# Multi-user infrastructure (plan — in progress)

> **Status:** planning, built collaboratively. This is the largest item on the
> stop-condition list (auth, `JournalEntry` schema change, Postgres, per-user
> keys, isolation). We're settling decisions one at a time before any code.

## The mental model

Today the app assumes **"one person, and all the data is theirs."** Multi-user
replaces that with **"whose is this?"** everywhere. Three stacked layers:

1. **Identity** — who are you? (login)
2. **Isolation** — you only ever see your own data. Every entry, mood score,
   search, synthesis, and chat answer is scoped to the logged-in user. This is
   the layer with real consequences — a leak here exposes someone's journal.
3. **Per-user config** — your settings and your API key/model become per-user
   (today they're global; `llm_client.py` already anticipates this).

## Decisions

### 1. Login: Sign in with Google (OAuth) ✅

Delegate identity to Google rather than storing passwords. Chosen because it's
the least code and least risk (no password hashes to protect), and because
**login and the GoodNotes/Drive import are the same OAuth consent** — one flow
grants both, per the 2026-06-19 decision.

- Anchor identity on Google's stable **`sub`** claim, never the email (email is
  mutable and can be reassigned → would mis-key accounts). Email is a display
  attribute.
- Mint our **own** `users.id` primary key; everything foreign-keys to that.
  Google's `sub` is just one unique column. This decouples *how* someone logged
  in from *who they are*, so adding another login method later touches one table.
- `users` table (starting shape): `id` (PK) · `google_sub` (unique) · `email` ·
  `created_at`; grows `display_name` and per-user `api_key`/`model` later.

### 2. Sessions: httpOnly session cookie ✅

After the one-time Google login, the browser needs to prove "still me" on every
request. Use a **server-side session** keyed by a random id in an **`httpOnly`**
cookie (JS can't read it → an XSS bug can't steal the login; contrast a JWT in
`localStorage`, which any injected script can read). Revocation is just deleting
the session row. JWT's statelessness-for-horizontal-scale advantage is a problem
we don't have.

- Cookie flags: `httpOnly`, `Secure`, `SameSite=Lax` (CSRF mitigation — the
  cookie is auto-sent, so an other-origin forged request is the risk; SameSite
  neutralises it, add a CSRF token if we ever need cross-site POSTs).
- Server stores sessions (table or signed server-side store). Fine at our scale.

### 3. Data model + isolation ✅ (app layer; RLS deferred to #4)

Schema: `JournalEntry` gains a `user_id` FK → `users.id` (this is the
schema-change stop condition; the `source`/content-hash design anticipated it).
Principle: **every read filters by the current user, every write stamps them** —
and *isolation is only as strong as the single weakest query*, so the goal is to
make forgetting the filter hard, not to rely on remembering.

Mechanism:
- **`get_current_user` dependency** — resolves the session cookie → `User`;
  raises 401 if absent (this is also the auth gate). Hands every endpoint the
  user with no boilerplate. **Provides** the user but doesn't **force** the
  filter, so pair with:
- **User-scoped data-access helpers** — entry reads funnel through helpers that
  take the user and always apply `WHERE user_id`, so "read entries" *is*
  "this user's entries" (can't call them without a user). The choke point.
- **Isolation tests** — actively attack the boundary (user B requests user A's
  entry id → empty/403), so a forgotten filter fails CI.
- **Postgres Row-Level Security** — optional DB-enforced backstop (the DB refuses
  to return other users' rows regardless of the query). SQLite can't do this →
  decided with #4.

**Vector store (Chroma) isolation:** semantic search hits Chroma, not SQL, so
`WHERE user_id` doesn't touch it — isolation must be re-applied per store. Stamp
`user_id` into each vector's metadata in `embed_and_store`; pass a
`where={"user_id": me}` filter on every `search_similar`. Make `search_similar`
require a user (same choke-point discipline).

**Dedup is per-user:** identical text from two users hashes the same, so global
`content_hash` dedup would drop the second user's entry. Make the dedup key
per-user — preferred as a **composite `(user_id, content_hash)`** (keeps the hash
a pure content fingerprint and `user_id` an orthogonal scoping dimension, vs.
hashing `user_id+content` together). `user_id` is the same scoping dimension
layered everywhere: SQL `WHERE`, Chroma metadata, dedup key.

### 5. Cost model: freemium (metered free tier + BYO-key upgrade) ✅

Public launch means a stranger's question bills *your* global key today — a real
surprise-bill risk. Chosen freemium over BYO-key-only because BYO-key's friction
is at the front door (most Reddit tryers have no Anthropic key → bounce). Free
tier on our key, BYO-key/paid as the *upgrade*.

Minimum safe mechanism:
- **Per-user usage meter, persisted** — extend `_log_usage` to store per-user
  cost/usage. This is also widget #13 (visibility and safety are one feature).
- **Quota check BEFORE the LLM call** — over limit → friendly refusal, no call
  (checking after = already spent the money).
- **Global daily cap / kill-switch** — backstop when headcount math is wrong or a
  flood arrives; per-user quota *and* a global ceiling.
- **Cheap model for the free tier** — free = `claude-haiku-4-5` (~5× cheaper),
  BYO/paid = opus. This is the per-user model config the `llm_client` comment
  anticipated.
- **BYO/paid keys are stored secrets** → encrypt at rest, never log, never return
  to the client (smaller surface than option 1, but still real).
- Metering needs identity → "try it" = **log in with Google, then try** (one tap;
  kills anonymous abuse). No truly-anonymous usage.

### 4. Database: Postgres (now) ✅

Reframed by the goal: **public launch (Reddit), concurrent strangers, and it's
practice for commercial work** → Postgres now, not "SQLite then migrate." SQLite's
single-writer lock stalls under concurrent load; deployed apps run multiple
instances that can't share a SQLite file; and for journals belonging to the
public, the RLS backstop is genuinely worth it.

- **RLS is now in (resolves #3's deferred backstop).** Write per-table policies
  scoping to the current user; the app sets the current user on each connection
  (`SET app.current_user_id = …`) so policies can enforce it. Sits *on top of*
  the app-layer isolation — even a forgotten `WHERE` can't leak.
- **Adopt Alembic for migrations.** The hand-rolled `ensure_schema()` (PRAGMA +
  ALTER) was a deliberate SQLite-only choice; Postgres + an evolving schema needs
  a real migration tool. New dependency (stop condition) — the right one.
- Local dev via Docker Postgres; managed Postgres in prod (Supabase/Neon/Fly/…).
  `DATABASE_URL` already exists in `config.py`; engine swap is URL + driver
  (psycopg). This DB choice is the clean candidate for its own future ADR section.

### 6. Migration of existing single-user data ✅

~962 existing entries predate `user_id`. Can't add a `NOT NULL user_id` to a
populated table directly. Canonical sequence (an Alembic data migration):
1. **Create the owner user** (you) — the orphan rows need an id to point at.
2. **Add `user_id` nullable** (no value exists for old rows yet).
3. **Backfill** `user_id = owner.id` on all existing rows.
4. **Tighten**: `NOT NULL` + FK; swap `UNIQUE(content_hash)` →
   `UNIQUE(user_id, content_hash)`; add RLS policies.
5. **Chroma**: backfill `user_id = owner` into every existing vector's metadata
   (per-store isolation again; cheaper than re-embedding).
6. Wrap it in the one-time **SQLite→Postgres** data load (export `journal.db`,
   import with owner stamped).

**Payoff of choosing composite `(user_id, content_hash)` (#3):** existing hashes
are already `hash(content)` → no recompute, just the constraint swap. Option (a)
(`hash(user_id+content)`) would have forced recomputing all 962. Design choices
show their bill at migration time.

---

## Status: planning pass complete

All six decisions locked. Next: distill into a single ADR
(`docs/adr/0003-multi-user-architecture.md`, with the Postgres/DB choice as its
own supersedable section) and sequence the build.
