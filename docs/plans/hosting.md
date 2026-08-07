# Hosting plan

Goal: a public showcase instance, ideally at no recurring cost, that is fast
enough on first click to be worth showing anyone.

## What the app actually needs

Measured on 2026-08-06 rather than estimated (M-series Mac, `.venv`):

| | Peak RSS |
|---|---|
| interpreter only | 10 MB |
| + fastapi / sqlmodel / anthropic | 80 MB |
| + fastembed imported | 119 MB |
| + MiniLM model loaded | 299 MB |
| + one 384-d embedding | 300 MB |

Model load 0.60 s, per-embed 16 ms. Add the app itself, the per-request BM25
index (`retrieval.py:90`), and connections: **~400 MB realistic peak.**

CPU matters more than it looks. Both figures above are on fast cores; a 0.1-vCPU
share is roughly 10–20× slower, which turns a 0.6 s model load into ~10 s and a
16 ms query embed into ~0.2–0.3 s. Steady-state search stays acceptable. Cold
start does not.

Always-on is *not* required for a showcase: `GOODNOTES_PATH` stays unset in
production so the APScheduler job never runs, and demo visitors get `/ingest`
403'd, so no fire-and-forget mood scoring is in flight to lose.

Postgres needs pgvector **≥ 0.8** for `hnsw.iterative_scan`.

## Options

| | Cost/mo | RAM | CPU | Sleeps | pgvector |
|---|---|---|---|---|---|
| Oracle Always Free | **$0** | 12 GB | 2 OCPU ARM | no | own image ✓ |
| Render free + Neon free | **$0** | 512 MB | 0.1 | 15 min → ~60 s wake | Neon ✓ |
| Fly + Neon free | ~$4 | 512 MB | shared | optional | Neon ✓ |
| Hetzner VPS | €4–5 | 4 GB | 2 vCPU | no | own image ✓ |

Notes from research, all 2026:

- **Oracle** halved Always Free ARM from 4 OCPU/24 GB to 2 OCPU/12 GB, enforced
  18 Aug 2026, with no public announcement. Still ~30× our need. The real risks
  are regional Ampere A1 capacity (a shape can be "Always Free" and simply
  unavailable in your home region) and idle reclaim.
- **Render** free is 512 MB / 0.1 CPU, spins down after 15 min, ~1 min to wake.
  400 MB peak inside 512 MB is uncomfortably tight.
- **Fly** removed free allowances for new signups — now a 2-hour trial. A
  512 MB always-on machine is ~$4/mo, so it does not undercut Hetzner.
- **Neon** free is permanent: 100 compute-hours/mo, 0.5 GB storage. Ample —
  the demo corpus is 65 entries and ~1,150 vectors is under 2 MB.

## Recommendation

**Build one portable artifact, try free, fall back to paid without rework.**

Every option above is either "a Linux box with Docker" or "one container", so
the same image runs on all of them. That means the provider decision does not
have to be made now — only the artifact shape does.

Make the artifact a **single container: FastAPI serves the built SPA** via a
`StaticFiles` mount. This is the enabling step, not a detail:

- Runs unchanged on Oracle, Hetzner, Fly, Render, or a machine at home.
- Same origin everywhere, so the httpOnly session cookie is never third-party.
- Deletes the whole prefix-stripping layer — `handle_path`, `ROOT_PATH`,
  `--root-path`, `VITE_API_BASE_URL=/api`, and registering the Google redirect
  URI with an `/api` segment. `request.url_for("callback")` becomes correct
  with no help.

Caddy stays, reduced to TLS plus one `reverse_proxy` line, and is only needed
on the VPS-shaped hosts.

Cost: the backend image needs a Node build stage and its build context moves
from `backend/` to the repo root, so the API image rebuilds on frontend-only
changes. Accepted deliberately — one deployable beats a marginally faster
asset path at this traffic.

## Steps

1. **Restructure to one container.** Multi-stage Dockerfile (node → python),
   `StaticFiles` mount with an SPA fallback that does *not* shadow API 404s,
   re-add cache headers for hashed assets and `GZipMiddleware`, revert the
   `ROOT_PATH` change, simplify `deploy/Caddyfile` and the compose file.
2. **Verify the arm64 build** if Oracle is in play. `pgvector/pgvector` is
   multi-arch and onnxruntime ships aarch64 wheels, but confirm rather than
   assume.
3. **Attempt Oracle Always Free.** Timebox it — capacity in a given home region
   is a lottery and not worth days of retrying.
4. **Fall back to Hetzner** if capacity does not appear. Same compose, same
   image, no rework.
5. **Database**: self-host Postgres in the same compose on either box. Neon
   free is the alternative if the host has no persistent disk.
6. **Verification** as in `deploy/README.md`: pgvector `extversion`, a real
   Google sign-in, a token-by-token synthesis stream, rate limits bucketing
   separately from two networks.

## Not doing

**Render free for the showcase.** It is genuinely $0 and zero hassle, but a
visitor arriving cold waits ~1 minute at a blank page. For a link whose entire
job is to make a good impression, that is worse than the €5 it saves. Fine as a
staging or throwaway target where you control when it is warm.

**Home hosting + Cloudflare Tunnel.** Free and always-on, but a showcase link
that depends on a machine at home staying awake is a link that is dead when it
matters. Reasonable later for the private instance.

## Open

Backups are unaddressed. `journal_pgdata` is the journal and nothing copies it
anywhere. Needed before real data lands on any of these.
