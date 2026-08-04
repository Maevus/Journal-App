# Journal Analytics API — backend image.
#
# Build from the backend/ directory:
#   docker build -t journal-api backend/
#
# The image is stateless: no journal.db, no vector directory, no model cache to
# warm at runtime. The only writable thing it needs is the Postgres it points at.
FROM python:3.13-slim AS base

# psycopg[binary] ships its own libpq, so no build toolchain is needed. curl is
# here for the healthcheck below.
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

# Bake the embedding model into the image. Without this, fastembed downloads
# ~90 MB from HuggingFace into a temp dir on first use — inside a request, on
# every cold start, and not at all if huggingface.co is unreachable. Baking it
# is what makes the "no stateful filesystem dependency" claim actually true.
ENV FASTEMBED_CACHE_DIR=/opt/fastembed
RUN python -c "\
from fastembed import TextEmbedding; \
TextEmbedding('sentence-transformers/all-MiniLM-L6-v2', cache_dir='/opt/fastembed')" \
    && chmod -R a+rX /opt/fastembed

COPY . .

# Migrations are a deploy step, not a boot step — see config.AUTO_MIGRATE. Run
# `alembic upgrade head` once per release before rolling instances.
ENV AUTO_MIGRATE=0

# Non-root. Nothing in /app is written at runtime.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# /trends answers without a database round-trip for an unauthenticated caller,
# so it proves the process is up but NOT that Postgres is reachable. Treat a
# green healthcheck as "serving", not "healthy end to end". It is deliberately
# exempt from rate limiting (services/rate_limit.py) — a throttled healthcheck
# makes the container restart itself under exactly the load it should survive.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/trends -o /dev/null || exit 1

# The trust boundary. This used to be `--forwarded-allow-ips "*"`, which tells
# uvicorn to believe any peer's X-Forwarded-For — so with nothing actually in
# front of the container an attacker sends a fresh address per request, every
# request lands in its own bucket, and every per-IP limit in
# services/rate_limit.py silently evaporates. Now it is one configurable value,
# read by both uvicorn and services/client_ip.py so the server and the app
# cannot disagree about who is allowed to speak for someone else.
#
# The default trusts only the loopback, i.e. effectively nothing: unset, the app
# keys on the socket peer, which is wrong behind a load balancer but safe. Set
# TRUSTED_PROXY_IPS to your proxy's address(es) at deploy time — comma-separated,
# CIDR allowed. See the manifest at the end of .env.example.
ENV TRUSTED_PROXY_IPS=127.0.0.1

# Single worker by default. Scaling out is fine, but pin SESSION_SECRET first
# (the OAuth state cookie is signed per-process) and keep AUTO_MIGRATE=0 so
# workers don't race to migrate. Rate limit counters are in Postgres precisely
# so that scaling out doesn't multiply every limit by the worker count.
#
# Via `sh -c` because the trusted-proxy list has to be expanded at run time
# rather than baked into a JSON argv at build time; `exec` hands the process
# straight to uvicorn so it is still PID 1 and receives SIGTERM on shutdown.
CMD ["sh", "-c", \
     "exec uvicorn main:app --host 0.0.0.0 --port 8000 \
      --proxy-headers --forwarded-allow-ips \"$TRUSTED_PROXY_IPS\""]
