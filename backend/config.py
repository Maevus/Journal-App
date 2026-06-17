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
