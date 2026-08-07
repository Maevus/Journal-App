# Woolgather — one image: FastAPI serving both the API and the built SPA.
#   docker build -f backend/Dockerfile -t journal .     # context is the repo root
#
# Stateless: the only writable thing it needs is the Postgres it points at.

FROM node:24-slim AS web

WORKDIR /web

# Lockfile first so a source change doesn't reinstall node_modules.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
# VITE_API_BASE_URL comes from frontend/.env.production — empty, i.e. same origin.
RUN npm run build


FROM python:3.13-slim AS base

# curl is for the healthcheck; psycopg[binary] ships its own libpq.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so a code change doesn't reinstall them.
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake the embedding model in; otherwise fastembed downloads ~90 MB inside the
# first request of every cold start, and fails entirely if HF is unreachable.
ENV FASTEMBED_CACHE_DIR=/opt/fastembed
RUN python -c "\
from fastembed import TextEmbedding; \
TextEmbedding('sentence-transformers/all-MiniLM-L6-v2', cache_dir='/opt/fastembed')" \
    && chmod -R a+rX /opt/fastembed

COPY backend/ .

# main.py mounts this at / when it exists, with an SPA fallback to index.html.
COPY --from=web /web/dist ./static

# Migrations are a deploy step, not a boot step. Run `alembic upgrade head` once
# per release, before rolling instances.
ENV AUTO_MIGRATE=0

# No /docs, /redoc or /openapi.json in a shipped image.
ENV ENABLE_DOCS=0

RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# /health is unauthenticated and does a SELECT 1, so this is green only when the
# process AND its database are up. Carries no rate limiter by design.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/health -o /dev/null || exit 1

# The trust boundary: which peers may set X-Forwarded-For. Too wide and an
# attacker forges one per request, giving every request its own rate-limit
# bucket. Read by both uvicorn and services/client_ip.py so they can't disagree.
# Default trusts only loopback; set to your proxy's address(es) at deploy time.
ENV TRUSTED_PROXY_IPS=127.0.0.1

# Single worker. To scale out: pin SESSION_SECRET (the OAuth state cookie is
# signed per-process) and keep AUTO_MIGRATE=0. Rate-limit counters already live
# in Postgres so N workers don't multiply every limit by N.
#
# `sh -c` to expand TRUSTED_PROXY_IPS at run time; `exec` so uvicorn is PID 1
# and receives SIGTERM.
CMD ["sh", "-c", \
     "exec uvicorn main:app --host 0.0.0.0 --port 8000 \
      --proxy-headers --forwarded-allow-ips \"$TRUSTED_PROXY_IPS\""]
