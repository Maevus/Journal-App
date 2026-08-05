# Journal Analytics API — backend image.
#   docker build -t journal-api backend/
#
# Stateless: the only writable thing it needs is the Postgres it points at.
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
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake the embedding model in; otherwise fastembed downloads ~90 MB inside the
# first request of every cold start, and fails entirely if HF is unreachable.
ENV FASTEMBED_CACHE_DIR=/opt/fastembed
RUN python -c "\
from fastembed import TextEmbedding; \
TextEmbedding('sentence-transformers/all-MiniLM-L6-v2', cache_dir='/opt/fastembed')" \
    && chmod -R a+rX /opt/fastembed

COPY . .

# Migrations are a deploy step, not a boot step. Run `alembic upgrade head` once
# per release, before rolling instances.
ENV AUTO_MIGRATE=0

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
