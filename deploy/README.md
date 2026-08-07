# Deploy

Single VPS, three containers: Caddy (TLS), the API, and Postgres. 512 MB is the
floor and will be tight — fastembed's ONNX runtime is ~150–250 MB resident and
BM25 rebuilds per search. 1–2 GB is comfortable.

The API image serves the built SPA itself (`main.py:_mount_spa`), so the app is
one origin at the root. That is deliberate: every call in `api/client.ts` sends
`credentials: 'include'` against an httpOnly session cookie, which a split origin
turns into a third-party cookie that Safari blocks by default. Nothing strips a
path prefix, so `request.url_for("callback")` is correct with no help.

## First deploy

DNS A/AAAA for `JOURNAL_DOMAIN` must point here before starting Caddy, or
certificate issuance fails. Ports 80 and 443 must be open.

```sh
git clone <repo> && cd Journal-App/deploy
cp .env.example .env          # fill it in
```

Bring up the database, migrate, then start everything:

```sh
docker compose -f docker-compose.prod.yml up -d --build db
docker compose -f docker-compose.prod.yml run --rm api alembic upgrade head
docker compose -f docker-compose.prod.yml up -d --build
```

The build context is the repo root: a node stage builds the SPA and the python
stage copies `dist` in. `VITE_API_BASE_URL` comes from `frontend/.env.production`
and is empty, i.e. relative to whatever host serves it.

Migrations are a deploy step, not a boot step (`AUTO_MIGRATE=0`) — concurrent
instances would otherwise race on `alembic upgrade head`.

## Releasing a change

```sh
git pull
docker compose -f docker-compose.prod.yml run --rm api alembic upgrade head
docker compose -f docker-compose.prod.yml up -d --build api
```

Frontend-only changes rebuild the API image too. Accepted: one deployable beats
a marginally faster asset path at this traffic.

## Verify after deploying

```sh
# pgvector >= 0.8, or filtered ANN silently loses recall as the table grows
docker compose -f docker-compose.prod.yml exec db \
  psql -U journal -d journal -c \
  "SELECT extversion FROM pg_extension WHERE extname = 'vector';"
```

- Sign in with Google end to end. The registered redirect URI is
  `https://<domain>/auth/callback` — no `/api` segment.
- Watch a synthesis stream render token by token. All at once at the end means
  response buffering.
- Hit a rate limit from two different networks and confirm they bucket
  separately. Shared bucket means `TRUSTED_PROXY_IPS` does not match Caddy's
  address; per-request reset means it is too wide.
- `curl -sI https://<domain>/assets/<hashed>.js | grep -i cache-control` →
  `immutable`; the same on `/` → `no-cache`.
- `curl -s https://<domain>/docs` returns the SPA, not the API explorer.

## Constraints worth knowing

- **One instance, always on.** The GoodNotes scheduler (`main.py:130`) and the
  fire-and-forget mood scoring (`main.py:108`) are both in-process. Replicas
  double the scans; scale-to-zero stops them silently.
- **`caddy_data` holds the certificates.** Deleting it re-requests them and can
  hit Let's Encrypt rate limits.
- **`journal_pgdata` is the journal.** Backed up by `backup/` — install the
  timer, and set `OFFSITE_TARGET`, which defaults to the same disk.
