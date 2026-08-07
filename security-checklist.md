# Woolgather — Pre-Launch Security Checklist

Stack: FastAPI + React/Vite/TS · Postgres · BYOK (user-supplied LLM API keys) · multi-user

The highest-value asset in this app is **other people's API keys**. Most of this list exists
to protect them. Work top-down; the first two sections are the ones that actually get exploited.

---
## 1. API key handling (BYOK)

- [ ] Decide: are keys stored server-side at all? Client-side-only means a DB dump leaks nothing.
- [ ] If stored: encrypted at rest with a key from the environment, **not** stored in the database.
- [ ] Encryption key is rotatable — document the rotation procedure, even if it's three lines.
- [ ] No endpoint returns the key. Audit every serializer/response model that touches the user object.
- [ ] UI displays last-4 only; the masked value is masked **server-side**, not in the frontend.
- [ ] `grep` the codebase for every read of the key field — each one either calls the provider API or masks.
- [ ] Keys never appear in URL query strings or path params.
- [ ] Failure path checked: what does the app do when a user's key is invalid or rate-limited?

## 2. Multi-tenant isolation (new surface — highest risk of a fresh bug)

- [ ] Every query filters on the **session-derived** user id, never a user id from the request body/params.
- [ ] Enumerate every endpoint that takes a resource id; confirm ownership is checked before returning.
- [ ] Test explicitly: log in as user A, request user B's entry / settings / vector results. Should 404.
- [ ] Vector search is scoped per user — no cross-tenant retrieval leakage in RAG results.
- [ ] Background jobs / exports carry the correct user scope.
- [x] Account deletion removes entries, embeddings, and keys — not just the user row.
      `POST /auth/delete-account` → `services/account.py`. Nine tables, children first,
      one transaction; `entry_embeddings` needs its own raw-SQL delete because it is
      outside `SQLModel.metadata`. Demo user guarded at route and service.
      Covered by `test_account_deletion.py` and `test_account_deletion_pg.py`.

## 3. Secrets & configuration

- [ ] `.env` in `.gitignore`, and **checked against git history**, not just the working tree.
- [ ] No secrets in the frontend bundle (anything in `VITE_*` is public — verify nothing sensitive is there).
- [ ] Production secrets come from the host's env/secret store; no `.env` file on the server.
- [ ] `DEBUG = False` in production; no stack traces returned to clients.
- [ ] CORS restricted to the actual frontend origin — not `*`.
- [ ] Default/dev credentials removed from config and seed scripts.

## 4. Logging & observability

- [ ] Request bodies are **not** logged on auth or settings routes.
- [ ] Error tracking (Sentry etc.) scrubs the key field and Authorization headers; breadcrumbs too.
- [ ] Provider API errors are logged without echoing the request payload.
- [ ] Journal entry content is not logged in full — it's sensitive personal data even when not a secret.
- [ ] Log retention is bounded.

## 5. Auth & sessions

- [ ] Passwords hashed with bcrypt/argon2 (never fast hashes), if not using an external identity provider.
- [ ] Session tokens: httpOnly + Secure + SameSite cookies, or short-lived JWTs with a refresh path.
- [x] Rate limiting on login, registration, and password reset. — `/auth/login` and
      `/auth/callback` are limited per IP (`RATE_LIMIT_AUTH_PER_MIN`, default 10).
      There is no registration or password reset to limit: identity is Google OAuth.
      **Depends on `TRUSTED_PROXY_IPS` being set correctly in production** — a
      per-IP limit is only as good as the address it keys on.
- [ ] Logout actually invalidates server-side.
- [ ] Password reset tokens are single-use and expiring.

## 6. Frontend / XSS

- [ ] Journal content is user-controlled — sanitise on render; avoid `dangerouslySetInnerHTML`.
- [ ] Content Security Policy set (blocks the XSS → key exfiltration path).
- [ ] API keys not held in `localStorage` if avoidable; if unavoidable, CSP and sanitisation are load-bearing.
- [ ] Markdown rendering (if any) uses a sanitising renderer.

## 7. Database (post-migration)

- [ ] App connects as a least-privilege role — not the Postgres superuser.
- [ ] Connection string uses TLS; DB not exposed to the public internet.
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
- [ ] Foreign key constraints enforced (SQLite let violations through; Postgres won't).

## 8. LLM-specific

- [ ] Journal entries are treated as **untrusted input** to the model, not as instructions.
- [ ] The model pipeline has no tools, outbound network calls, or key access it doesn't need.
- [x] Per-user spend/rate limits, so one account can't burn a key or the service.
      Spend: the quota gate (`services/quota.py`) — per-user daily budget plus the
      `GLOBAL_DAILY_USD` kill-switch. Rate: `services/rate_limit.py` — per-identity
      fixed windows on chat, synthesis, ingest, keys and search, keyed on the user
      id when signed in and the address when not. BYO-key holders are exempt from
      the spend cap and deliberately still subject to the rate limit.
- [ ] Prompts and completions are not persisted in logs by default.

## 9. Deployment

- [x] HTTPS enforced; HTTP redirects. HSTS set. — Caddy terminates TLS and adds the
      HTTP→HTTPS redirect itself; `Strict-Transport-Security` is set in the Caddyfile.
- [ ] Security headers: `X-Content-Type-Options`, `X-Frame-Options` / frame-ancestors, Referrer-Policy.
- [ ] Dependencies audited (`pip-audit`, `npm audit`) and pinned.
- [x] Admin/docs endpoints (`/docs`, `/redoc`) disabled or authenticated in production.
      `ENABLE_DOCS=0` in the image gates `/docs`, `/redoc` and `/openapi.json`.
      Note the code default is **on** — a non-Docker production run would expose them.
- [ ] Health check endpoint leaks no version or config detail.

## 10. Legal / user trust

- [ ] Privacy note stating what's stored, that keys are encrypted, and that entries are sent to the model provider.
- [ ] Users can export their data.
- [x] Users can delete their account and everything in it. — backend done (§2); still
      needs a frontend affordance, so it is not yet reachable by a user.
- [ ] A contact route for reporting a security issue.

---

### Minimum bar before going live

If time runs out, these five are non-negotiable:

1. Keys encrypted at rest and never returned by any endpoint.
2. Every query scoped to the session user (tested by trying to break it).
3. No secrets in git history or the frontend bundle.
4. Request bodies scrubbed from logs and error tracking.
5. HTTPS + backups with one tested restore.