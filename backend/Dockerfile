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
# green healthcheck as "serving", not "healthy end to end".
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/trends -o /dev/null || exit 1

# Single worker by default. Scaling out is fine, but pin SESSION_SECRET first
# (the OAuth state cookie is signed per-process) and keep AUTO_MIGRATE=0 so
# workers don't race to migrate.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
