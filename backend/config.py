"""Central runtime config.

Postgres is the only database the app runs on — no SQLite fallback; a
missing DATABASE_URL is an error, not a silent second source of truth.
(Tests use their own throwaway in-memory SQLite, untouched by this path.)

APP_MODE selects the demo user and Keep-sync default, not the database —
both modes share one Postgres, and demo-ness is per-user (services/auth.py).

  APP_MODE=real  (default) → sync on
  APP_MODE=demo            → sync off, requests resolve to the demo user
"""
import os
import secrets
from datetime import timedelta

_MODES = {"real", "demo"}

APP_MODE = os.getenv("APP_MODE", "real").lower()
if APP_MODE not in _MODES:
    raise ValueError(f"APP_MODE must be one of {sorted(_MODES)}, got {APP_MODE!r}")

IS_DEMO = APP_MODE == "demo"

# No default — tests set their own engine directly and never import this path.
DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is required and must be Postgres — e.g. "
        "postgresql+psycopg://journal:journal@localhost:5432/journal. "
        "Start one with `cd backend && docker compose up -d db`, or use "
        "`scripts/dev.sh {real|demo}`, which sets it for you."
    )
if not DATABASE_URL.startswith(("postgresql", "postgres")):
    raise RuntimeError(
        f"DATABASE_URL must be Postgres, got {DATABASE_URL!r}. SQLite is no "
        "longer supported: pgvector search, the quota advisory lock and the "
        "rate-limit upsert all need Postgres, and two engines meant two journals."
    )

# Demo must never pull real Keep notes, so sync defaults off in demo mode.
SYNC_ON_STARTUP = os.getenv(
    "SYNC_ON_STARTUP", "0" if IS_DEMO else "1"
).lower() not in ("0", "false", "no")

# Minutes between scheduled GoodNotes folder scans. 0 (default) disables the
# scheduler; a positive value polls for newly-appended pages.
GOODNOTES_SCAN_INTERVAL_MIN = int(os.getenv("GOODNOTES_SCAN_INTERVAL_MIN", "0"))

# --- Freemium quotas (ADR 0003, F2) ---
# Daily LLM budgets in dollars, UTC-day window, read at call time by
# services/quota.py. Free tier defaults to $0 (BYO key unlocks LLM use, F4).
FREE_TIER_DAILY_USD = float(os.getenv("FREE_TIER_DAILY_USD", "0"))

# --- Freemium model resolution (ADR 0003, F3) ---
# Which model each tier runs; env-tunable. Pricing is data-driven in
# llm_client._PRICING, keyed on whatever string resolves here.
FREE_TIER_MODEL = os.getenv("FREE_TIER_MODEL", "claude-haiku-4-5")
DEMO_MODEL = os.getenv("DEMO_MODEL", "claude-opus-4-8")

# --- Freemium BYO keys (ADR 0003, F4) ---
# Fernet key (32 bytes, urlsafe-base64) encrypting users' API keys at rest.
# Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Must stay STABLE across restarts or stored keys become undecryptable.
# Unset = BYO-key feature off (services/key_store.py errors only on use).
APP_ENCRYPTION_KEY = os.getenv("APP_ENCRYPTION_KEY")

# BYO-key users pay their own way, so they get the premium model, not free-tier.
BYO_KEY_MODEL = os.getenv("BYO_KEY_MODEL", "claude-opus-4-8")

# The demo user gets its own small daily budget (else the $0 free tier would
# refuse the showcase), applied per-user since one instance serves both the
# demo visitor and real logged-in users.
DEMO_DAILY_USD = float(os.getenv("DEMO_DAILY_USD", "1"))

# When on, an unauthenticated request resolves to the demo user (read-only)
# instead of 401. Off (default) = private instance. Distinct from
# APP_MODE=demo, which resolves EVERY request, even logged-in ones.
PUBLIC_DEMO = os.getenv("PUBLIC_DEMO", "0").lower() not in ("0", "false", "no", "")

# Service-wide daily spend cap — the kill-switch bounding the worst day our
# API key can have. Defaults on: a kill-switch that defaults off isn't one.
GLOBAL_DAILY_USD = float(os.getenv("GLOBAL_DAILY_USD", "5"))

# How long the quota gate counts a reservation — headroom claimed by a call
# that started but hasn't been metered (models.usage.QuotaReservation),
# bounding how long a crashed call's claim stays stranded. Must outlast the
# slowest legitimate stream.
RESERVATION_TTL = timedelta(minutes=int(os.getenv("RESERVATION_TTL_MINUTES", "15")))

# --- Auth: Sign in with Google (ADR 0003) ---
# OAuth client creds (Google Cloud Console → Credentials → OAuth client ID,
# type "Web application"). Distinct from the Keep-ingestion creds below.
GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")

# Signs the short-lived OAuth `state` cookie between /auth/login and
# /auth/callback. Unset → random per boot (fine in dev); production must
# pin it so logins survive restarts and multiple workers.
SESSION_SECRET = os.getenv("SESSION_SECRET") or secrets.token_hex(32)

# Where the callback sends the browser after login — the SPA's origin.
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")

# Runs `alembic upgrade head` at startup. Convenient in dev; wrong in
# production (DDL on real data with no checkpoint, N workers racing) — set
# AUTO_MIGRATE=0 there and migrate as an explicit deploy step.
AUTO_MIGRATE = os.getenv("AUTO_MIGRATE", "1").lower() not in ("0", "false", "no", "")

# `Secure` on the session cookie, explicit rather than derived from
# request.url.scheme — behind a TLS-terminating proxy that reads plain http
# and would ship the token unprotected. Defaults on; browsers accept Secure
# cookies on localhost, so dev is unaffected.
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "1").lower() not in ("0", "false", "no", "")

# Where fastembed caches the ~90 MB embedding model. Unset lands in system
# temp, which scale-to-zero hosts wipe — turning the first search of each
# cold start into a synchronous download. backend/Dockerfile bakes it in.
FASTEMBED_CACHE_DIR = os.getenv("FASTEMBED_CACHE_DIR", "")

# --- Trust boundary: which peers may speak for another caller ---
# CIDR-allowed addresses of the reverse proxy in front of the app — the sole
# input to services/client_ip.py, which every per-IP rate limit keys on.
# Unset (default) ignores X-Forwarded-For outright. Wrong silently either
# way: too wide ("*") lets an attacker forge XFF and void every per-IP
# limit; too narrow (unset behind a real balancer) buckets the whole
# internet as one caller. backend/Dockerfile passes this same value to
# uvicorn's --forwarded-allow-ips so the two agree.
TRUSTED_PROXY_IPS = os.getenv("TRUSTED_PROXY_IPS", "")

# --- Per-identity rate limits (docs/decisions.md, 2026-08-04) ---
# Requests allowed per fixed window, per caller, per route tier — enforced by
# services/rate_limit.py. Two windows per tier: per-minute bounds a burst,
# per-hour bounds a burst-shaped abuser pacing itself all day. 0 disables
# that window (also the per-tier escape hatch); no global off switch, on
# purpose.
#
# Budgets reflect what a request COSTS US:
#   chat       three LLM calls per request (condense, date parse, stream)
#   synthesis  one LLM call, LRU-cached — a repeat request is free
#   ingest     inline OCR per PDF page + a background scoring call per entry
#   keys       a live Anthropic auth check, no quota gate, password-guessing surface
#   search     a local ONNX encode — CPU, not money, so the ceiling is higher
#   auth       the login handshake; always keyed per IP, never per user
# Cheap reads (/trends, /entries, /usage, /auth/me) and /health (the
# container HEALTHCHECK every 30s) carry no limiter.
RATE_LIMIT_CHAT_PER_MIN = int(os.getenv("RATE_LIMIT_CHAT_PER_MIN", "5"))
RATE_LIMIT_CHAT_PER_HOUR = int(os.getenv("RATE_LIMIT_CHAT_PER_HOUR", "40"))
RATE_LIMIT_SYNTHESIS_PER_MIN = int(os.getenv("RATE_LIMIT_SYNTHESIS_PER_MIN", "10"))
RATE_LIMIT_SYNTHESIS_PER_HOUR = int(os.getenv("RATE_LIMIT_SYNTHESIS_PER_HOUR", "60"))
RATE_LIMIT_INGEST_PER_MIN = int(os.getenv("RATE_LIMIT_INGEST_PER_MIN", "5"))
RATE_LIMIT_INGEST_PER_HOUR = int(os.getenv("RATE_LIMIT_INGEST_PER_HOUR", "30"))
RATE_LIMIT_KEYS_PER_MIN = int(os.getenv("RATE_LIMIT_KEYS_PER_MIN", "5"))
RATE_LIMIT_KEYS_PER_HOUR = int(os.getenv("RATE_LIMIT_KEYS_PER_HOUR", "20"))
RATE_LIMIT_SEARCH_PER_MIN = int(os.getenv("RATE_LIMIT_SEARCH_PER_MIN", "30"))
RATE_LIMIT_SEARCH_PER_HOUR = int(os.getenv("RATE_LIMIT_SEARCH_PER_HOUR", "0"))
RATE_LIMIT_AUTH_PER_MIN = int(os.getenv("RATE_LIMIT_AUTH_PER_MIN", "10"))
RATE_LIMIT_AUTH_PER_HOUR = int(os.getenv("RATE_LIMIT_AUTH_PER_HOUR", "0"))
