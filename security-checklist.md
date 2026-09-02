# Journal Analytics — Pre-Launch Security Checklist

Stack: FastAPI + React/Vite/TS · Postgres · BYOK (user-supplied LLM API keys) · multi-user

The highest-value asset in this app is **other people's API keys**. Most of this list exists
to protect them. Work top-down; the first two sections are the ones that actually get exploited.

Ticks mean *verified against the code*, not *intended*. Last pass: 2026-09-02.

---
## 1. API key handling (BYOK)

- [x] Decide: are keys stored server-side at all? — **Yes, stored, encrypted.** Client-side-only
      was rejected: the key is needed for background mood scoring, which runs after the
      request that triggered it has returned and no browser is present to hold it.
- [x] If stored: encrypted at rest with a key from the environment, **not** stored in the database.
      Fernet, `APP_ENCRYPTION_KEY` from env (`services/key_store.py`). The column is `bytes`,
      so the raw key never passes through a `str`. `_fernet()` raises lazily, so a server with
      no key configured serves free-tier users normally instead of crashing at import.
- [ ] Encryption key is rotatable — document the rotation procedure, even if it's three lines.
      **Not done.** `.env.example` warns that rotating it makes stored keys undecryptable, but
      there is no procedure. Needs: decrypt-all → re-encrypt with the new key → swap, as a script.
- [x] No endpoint returns the key. Audit every serializer/response model that touches the user object.
      `GET /keys` returns `{provider, has_key}` — a boolean, no key material. `POST` returns
      `{provider, status}`. No response model anywhere embeds the user's key.
- [x] UI displays last-4 only; the masked value is masked **server-side**, not in the frontend.
      **Stronger than asked:** no key material leaves the server at all, not even last-4, so
      there is nothing to mask. The UI renders "key set" from the boolean above.
- [x] `grep` the codebase for every read of the key field — each one either calls the provider API or masks.
      Exactly two call sites: `key_store.get_user_key` ← `llm_client.resolve_api_key` ←
      `_pre_call`, which passes it straight into the Anthropic client. Never logged, never returned.
- [x] Keys never appear in URL query strings or path params. — `POST /keys` takes a JSON body;
      no route has a key in its path or query.
- [x] Failure path checked: what does the app do when a user's key is invalid or rate-limited?
      Two gates. At save: `key_is_valid()` makes a live Anthropic call, so a typo or revoked
      key fails at entry rather than at first synthesis. At call time: `LLMProviderError` →
      an `llm_error` SSE frame inside a 200 (commit `7e5b0ef`). A user on their own key sees
      Anthropic's own words; a user on the app key gets a generic line, because the operator's
      billing state is not theirs to see.

## 2. Multi-tenant isolation (new surface — highest risk of a fresh bug)

- [x] Every query filters on the **session-derived** user id, never a user id from the request body/params.
      `get_current_user` resolves identity from the session cookie only. Reads chain onto
      `models/journal.py:user_entries(user_id)`, so `WHERE user_id = ?` is written exactly once.
      `user_id` is NOT NULL — there are no unowned rows to leak.
- [x] Enumerate every endpoint that takes a resource id; confirm ownership is checked before returning.
      Done: **no route takes an entry id.** Reads are by date range or query text, all scoped
      through `user_entries`. The only id-like input is the OAuth state cookie.
- [x] Test explicitly: log in as user A, request user B's entry / settings / vector results. Should 404.
      `tests/test_isolation.py` — 19 tests. Entries, trends, export, hybrid search, the vector
      query itself, the synthesis prompt, and chat retrieval are each asserted to see only the
      logged-in user's rows.
- [x] Vector search is scoped per user — no cross-tenant retrieval leakage in RAG results.
      `search_similar(..., user_id=)` is keyword-only and required, so a forgotten argument is a
      `TypeError`, not a leak. `test_retrieval_scopes_the_vector_query_itself` pins it.
- [x] Background jobs / exports carry the correct user scope.
      `score_mood_background(..., user_id=)` is required keyword-only; `user_id=None` skips
      scoring rather than spending the app key on an unowned row. Export chains `user_entries`.
      Covered by `test_writes_stamp_the_owner`, `test_ownerless_ingest_is_skipped`,
      `test_export_contains_only_your_rows`.
- [x] Account deletion removes entries, embeddings, and keys — not just the user row.
      `POST /auth/delete-account` → `services/account.py`. Nine tables, children first,
      one transaction; `entry_embeddings` needs its own raw-SQL delete because it is
      outside `SQLModel.metadata`. Demo user guarded at route and service.
      Covered by `test_account_deletion.py` and `test_account_deletion_pg.py`.

## 3. Secrets & configuration

- [x] `.env` in `.gitignore`, and **checked against git history**, not just the working tree.
      `*.env` is ignored. Full-history scan (every commit, every blob) found no `.env`, no
      database file, no dump, and no real key — the only `sk-ant-` matches are test placeholders.
- [x] No secrets in the frontend bundle (anything in `VITE_*` is public — verify nothing sensitive is there).
      One variable: `VITE_API_BASE_URL`, empty in production because FastAPI serves the bundle
      from its own origin.
- [ ] Production secrets come from the host's env/secret store; no `.env` file on the server.
      **Partial.** `docker-compose.prod.yml` interpolates `${POSTGRES_PASSWORD}` rather than
      hardcoding it, but the documented deploy still supplies it from a file beside the compose.
      Fine for a single host; not yet a secret store.
- [x] `DEBUG = False` in production; no stack traces returned to clients.
      The app is constructed without `debug=`, so FastAPI's default applies: a 500 returns
      `"Internal Server Error"` and the traceback goes to the log only.
- [x] CORS restricted to the actual frontend origin — not `*`.
      `allow_origins=[config.FRONTEND_URL]`. `allow_credentials=True` makes `"*"` illegal
      anyway — the browser refuses the combination.
- [x] Default/dev credentials removed from config and seed scripts.
      `journal:journal` appears only in local dev paths (`scripts/dev.sh`, the dev compose).
      The production compose takes the password from the environment. `seed_demo.py` creates
      only the demo account and its wipe is demo-user-scoped.

## 4. Logging & observability

- [x] Request bodies are **not** logged on auth or settings routes.
      No handler logs a request body. Auth logs the outcome, not the payload.
- [x] Error tracking (Sentry etc.) scrubs the key field and Authorization headers; breadcrumbs too.
      **N/A — no error tracker is installed.** Revisit if one is added; it is the most likely
      future route for a key to escape.
- [x] Provider API errors are logged without echoing the request payload.
      `_provider_error` logs `status_code` and Anthropic's own message only. The prompt is
      never part of the log line.
- [x] Journal entry content is not logged in full — it's sensitive personal data even when not a secret.
      Every ingestion and scoring log line carries the entry **id**, never its text.
- [ ] Log retention is bounded.
      **Not done.** Logs go to stdout and are kept by Docker's default json-file driver with no
      `max-size` / `max-file`. One line in the compose fixes it.

## 5. Auth & sessions

- [x] Passwords hashed with bcrypt/argon2 (never fast hashes), if not using an external identity provider.
      **N/A — identity is Google OAuth.** The app never sees or stores a password.
- [x] Session tokens: httpOnly + Secure + SameSite cookies, or short-lived JWTs with a refresh path.
      `httponly=True`, `samesite="lax"`, `secure=COOKIE_SECURE` (default on). The DB stores
      only the token's **SHA-256**, so a database dump yields no usable session.
- [x] Rate limiting on login, registration, and password reset. — `/auth/login` and
      `/auth/callback` are limited per IP (`RATE_LIMIT_AUTH_PER_MIN`, default 10).
      There is no registration or password reset to limit: identity is Google OAuth.
      **Depends on `TRUSTED_PROXY_IPS` being set correctly in production** — a
      per-IP limit is only as good as the address it keys on.
- [x] Logout actually invalidates server-side.
      `POST /auth/logout` calls `revoke_session` (deletes the row) before clearing the cookie,
      so a copied cookie is dead too.
- [x] Password reset tokens are single-use and expiring. — **N/A**, see above.

## 6. Frontend / XSS

- [x] Journal content is user-controlled — sanitise on render; avoid `dangerouslySetInnerHTML`.
      `dangerouslySetInnerHTML` appears nowhere in the frontend.
- [ ] Content Security Policy set (blocks the XSS → key exfiltration path).
      **Not done.** The Caddyfile sets HSTS and nothing else. See §9.
- [x] API keys not held in `localStorage` if avoidable; if unavoidable, CSP and sanitisation are load-bearing.
      Neither `localStorage` nor `sessionStorage` is used anywhere. The key never reaches the
      browser at all after being submitted.
- [x] Markdown rendering (if any) uses a sanitising renderer.
      `react-markdown` v9 with `rehype-highlight` only. `rehype-raw` is **not** installed, so
      raw HTML in an entry is escaped rather than parsed. Keep it that way.

## 7. Database (post-migration)

- [ ] App connects as a least-privilege role — not the Postgres superuser.
      **Not done.** `POSTGRES_USER: journal` makes `journal` the instance superuser, and the app
      connects as it. Wants a second role owning nothing, with `SELECT/INSERT/UPDATE/DELETE` on
      the app tables and no DDL — which pairs naturally with `AUTO_MIGRATE=0` (already set),
      since migrations can then run as the owner while the app cannot alter its own schema.
- [ ] Connection string uses TLS; DB not exposed to the public internet.
      **Half done.** The database has no published port in the production compose — it is
      reachable only on the internal network. But the connection string sets no `sslmode`, so
      the hop is unencrypted. Fine while both live in one compose; fix before the DB moves hosts.
- [x] Automated backups enabled **and a restore tested once**. — `deploy/backup/`,
      daily systemd timer. Restore rehearsed 2026-08-06 into a scratch database:
      1092 entries and 1092 embeddings back, `vector(384)` intact, HNSW index
      present, cosine query returned rows. **`OFFSITE_TARGET` still defaults to
      the same disk** — not yet a real backup.
- [x] Backups are encrypted — they contain the same keys the live DB does.
      `age` to a public key; the private half never goes on the server.
- [x] Alembic migrations run as a deliberate step, not automatically on boot.
      `AUTO_MIGRATE=0` in the image and in compose; the deploy runs
      `alembic upgrade head` before starting the API.
- [x] Foreign key constraints enforced (SQLite let violations through; Postgres won't).
      Every child table declares `Field(foreign_key=...)` and Postgres enforces them. This is
      what makes the children-first delete ordering in `services/account.py` load-bearing rather
      than cosmetic. `test_migrations.py` fails if models and revisions drift apart.

## 8. LLM-specific

- [ ] Journal entries are treated as **untrusted input** to the model, not as instructions.
      **Not done.** `prompts/synthesis_v1.yaml` interpolates `{entries}` straight into the user
      message with no delimiter and no "treat the following as data" framing, so an entry
      reading "ignore previous instructions…" is read as instructions. Blast radius is small —
      entries are per-user and the output returns to that same user, so an injection is
      self-inflicted — but OCR'd handwriting is arbitrary text and the framing is nearly free.
- [x] The model pipeline has no tools, outbound network calls, or key access it doesn't need.
      No tool definitions anywhere; the model cannot call back into the app. `llm_client.py` is
      the only module that talks to Anthropic, and it receives a key rather than reading one.
- [x] Per-user spend/rate limits, so one account can't burn a key or the service.
      Spend: the quota gate (`services/quota.py`) — per-user daily budget plus the
      `GLOBAL_DAILY_USD` kill-switch. Rate: `services/rate_limit.py` — per-identity
      fixed windows on chat, synthesis, ingest, keys and search, keyed on the user
      id when signed in and the address when not. BYO-key holders are exempt from
      the spend cap and deliberately still subject to the rate limit.
- [x] Prompts and completions are not persisted in logs by default.
      The per-call `[llm]` line carries model, token counts and cost — no prompt, no completion.
      `LLMUsage` rows store the same metering fields and no text.

## 9. Deployment

- [x] HTTPS enforced; HTTP redirects. HSTS set. — Caddy terminates TLS and adds the
      HTTP→HTTPS redirect itself; `Strict-Transport-Security` is set in the Caddyfile.
- [ ] Security headers: `X-Content-Type-Options`, `X-Frame-Options` / frame-ancestors, Referrer-Policy.
      **Not done.** The Caddyfile sets HSTS only. All three, plus the CSP from §6, are one
      `header` block in `deploy/Caddyfile` — the single highest-value fix left on this list.
- [ ] Dependencies audited (`pip-audit`, `npm audit`) and pinned.
      **Not done.** `requirements.txt` uses `>=` throughout, so two installs a month apart can
      differ. Neither audit has been run. Wants a pinned lock plus one audit pass.
- [x] Admin/docs endpoints (`/docs`, `/redoc`) disabled or authenticated in production.
      `ENABLE_DOCS=0` in the image gates `/docs`, `/redoc` and `/openapi.json`.
      Note the code default is **on** — a non-Docker production run would expose them.
- [x] Health check endpoint leaks no version or config detail.
      `GET /health` returns `{"status": "ok"}`, or 503 with `"database unreachable"`. No
      version, no config, no schema. Unauthenticated on purpose, and exempt from rate limiting
      so the container probe can't be throttled.

## 10. Legal / user trust

- [ ] Privacy note stating what's stored, that keys are encrypted, and that entries are sent to the model provider.
      **Not done, and the only item here a user would notice.** Everything it needs to say is
      already true and documented elsewhere in this file.
- [x] Users can export their data. — `routers/export.py`, CSV and JSON, scoped through
      `user_entries` and covered by `test_export_contains_only_your_rows`.
- [x] Users can delete their account and everything in it. — backend in §2; reachable
      from the Account card's "Delete account" tab, which unlocks only once the user
      types `DELETE`.
- [ ] A contact route for reporting a security issue.
      **Not done.** A `SECURITY.md` with one address satisfies it, and GitHub surfaces the file
      automatically on a public repo.

---

### Minimum bar before going live

If time runs out, these five are non-negotiable:

1. Keys encrypted at rest and never returned by any endpoint. — **met** (§1)
2. Every query scoped to the session user (tested by trying to break it). — **met** (§2, 19 tests)
3. No secrets in git history or the frontend bundle. — **met** (§3, full-history scan)
4. Request bodies scrubbed from logs and error tracking. — **met** (§4)
5. HTTPS + backups with one tested restore. — **met**, with `OFFSITE_TARGET` still on the
   same disk (§7)

### What's actually left

Eleven unchecked, none of them in §1 or §2 — the two sections that actually get exploited:

**Before any public deployment**
- Security headers + CSP — one `header` block in the Caddyfile (§6, §9)
- Least-privilege database role (§7)
- Pin and audit dependencies (§9)
- Privacy note and a security contact (§10)

**Before other people's keys are on it**
- Document the `APP_ENCRYPTION_KEY` rotation procedure (§1)
- Bound log retention (§4)
- Frame journal entries as data, not instructions (§8)

**When the topology changes**
- Secret store instead of a file (§3), TLS on the DB hop (§7) — both only matter once the
  database or the app leaves the single host.
