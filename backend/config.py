"""
Central runtime config.

A single APP_MODE switch derives the database, vector store, and Keep-sync
defaults together, so they can't drift apart — the dangerous case being a demo
DB selected while Keep sync is left on, which would pull real notes into the
demo. One switch can't be half-set.

  APP_MODE=real  (default) → journal.db      + chroma_db      + sync on
  APP_MODE=demo            → demo_journal.db + demo_chroma_db + sync off

Any individual var still wins when set explicitly, so every combination is
still reachable:

  APP_MODE=demo uvicorn main:app                       # whole demo stack
  DATABASE_URL=sqlite:///./scratch.db uvicorn main:app # one override
"""
import os
import secrets

_MODES = {"real", "demo"}

APP_MODE = os.getenv("APP_MODE", "real").lower()
if APP_MODE not in _MODES:
    raise ValueError(f"APP_MODE must be one of {sorted(_MODES)}, got {APP_MODE!r}")

IS_DEMO = APP_MODE == "demo"

# Mode-derived defaults — an explicitly-set env var always overrides these.
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///./demo_journal.db" if IS_DEMO else "sqlite:///./journal.db",
)
CHROMA_PERSIST_DIR = os.getenv(
    "CHROMA_PERSIST_DIR",
    "./demo_chroma_db" if IS_DEMO else "./chroma_db",
)
# Demo must never pull real Keep notes, so sync defaults off in demo mode.
SYNC_ON_STARTUP = os.getenv(
    "SYNC_ON_STARTUP", "0" if IS_DEMO else "1"
).lower() not in ("0", "false", "no")

# Minutes between scheduled GoodNotes folder scans. 0 (default) disables the
# scheduler entirely; a positive value starts an interval job at startup that
# picks up newly-appended pages of the growing GoodNotes PDF backup.
GOODNOTES_SCAN_INTERVAL_MIN = int(os.getenv("GOODNOTES_SCAN_INTERVAL_MIN", "0"))

# --- Freemium quotas (ADR 0003, F2) ---
# Daily LLM budgets in dollars over a UTC-day window, read at call time by
# services/quota.py. The launch posture (docs/decisions.md, 2026-07-03): the
# free tier costs us $0 — keyless users get budget $0 and a friendly refusal;
# LLM features unlock with a BYO key (F4). Flipping to a subsidized budget
# later is an env change, not a redesign.
FREE_TIER_DAILY_USD = float(os.getenv("FREE_TIER_DAILY_USD", "0"))

# Demo mode logs every visitor in as the seeded demo user, so the $0 free
# tier would refuse the whole showcase (chat always, synthesis on any cache
# miss). The demo gets its own small budget instead — bounded per day and
# backstopped by the global cap.
DEMO_DAILY_USD = float(os.getenv("DEMO_DAILY_USD", "1"))

# Service-wide daily spend cap across ALL users and system work — the
# kill-switch that bounds the worst day our API key can have. Defaults to a
# real number rather than "unset = uncapped": a kill-switch that defaults to
# off isn't one.
GLOBAL_DAILY_USD = float(os.getenv("GLOBAL_DAILY_USD", "5"))

# --- Auth: Sign in with Google (ADR 0003) ---
# OAuth client credentials from Google Cloud Console → Credentials → OAuth
# client ID (type "Web application", redirect URI http://localhost:8000/auth/callback).
# Distinct from GOOGLE_EMAIL/GOOGLE_APP_MASTER_TOKEN, which are Keep-ingestion creds.
GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")

# Signs the short-lived SessionMiddleware cookie that carries the in-flight
# OAuth `state` between /auth/login and /auth/callback. Unset → random per
# boot, which is fine in dev (the only casualty is a login mid-restart);
# production must pin it so logins survive restarts and multiple workers.
SESSION_SECRET = os.getenv("SESSION_SECRET") or secrets.token_hex(32)

# Where the callback sends the browser after login — the SPA's origin.
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")
