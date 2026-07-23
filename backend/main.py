import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()

# Import after load_dotenv so APP_MODE / DB paths reflect .env, not just the shell.
import config  # noqa: E402

# Import models so SQLModel registers their tables before create_all
import models.journal  # noqa: F401, E402
import models.keys  # noqa: F401, E402
import models.user  # noqa: F401, E402
from models.journal import engine
from routers import auth, chat, entries, export, ingest, keys, search, synthesis, trends, usage

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    """Make app-module logs visible.

    Uvicorn configures only its own `uvicorn.*` loggers and leaves the root
    logger handlerless, so records from `services.*` / `routers.*` fall through
    to the WARNING-level lastResort handler — every `logger.info` is silently
    dropped (mood-scoring skips, quota refusals, OAuth-callback failures, the
    launch banner). Attach one INFO StreamHandler to the root so they reach
    stdout (and, under scripts/dev.sh, the log file). Uvicorn's own loggers keep
    `propagate=False`, so its access/error lines don't double up here.

    Lives in the lifespan, not at import: the TestClient skips lifespan (see
    conftest), so tests keep pytest's own log capture untouched. Idempotent so a
    second startup in one process doesn't stack handlers.
    """
    root = logging.getLogger()
    if getattr(root, "_journal_configured", False):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    root._journal_configured = True  # type: ignore[attr-defined]


def _run_migrations() -> None:
    """Bring the database schema up to head via Alembic — the replacement for
    the old create_all + ensure_schema() startup.

    Best-effort and logged (like the Keep sync below): a fresh DB is built to
    head; an already-migrated DB is a no-op. Kept in the lifespan so plain
    `uvicorn main:app` still self-migrates. A legacy pre-Alembic DB (tables but
    no alembic_version) needs a one-time `alembic stamp head` — the demo seed and
    the SQLite→Postgres copy both stamp/upgrade, so this only surprises a stray
    old journal.db, which now logs a clear warning instead of failing boot.
    """
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parent / "alembic"))
    try:
        command.upgrade(cfg, "head")
    except Exception:  # noqa: BLE001
        logger.exception(
            "alembic upgrade failed; if this is a legacy pre-Alembic DB, run "
            "`alembic stamp head` once. Continuing with the existing schema."
        )


def _sync_on_startup_enabled() -> bool:
    """Sync Keep when the app opens, unless disabled. Off is useful under
    `uvicorn --reload` (restarts re-sync on every save) and is the default in
    demo mode so real notes never leak into the demo DB."""
    if not config.SYNC_ON_STARTUP:
        return False
    return bool(os.getenv("GOOGLE_EMAIL"))  # no creds → nothing to sync


async def _startup_keep_sync() -> None:
    """Pull new Keep notes once at startup, then mood-score them. Best-effort:
    any failure is logged and the app continues serving existing data."""
    from services.ingestion import ingest_keep, score_mood_background

    try:
        with Session(engine) as session:
            stats, new_entries = await ingest_keep(session)
            to_score = [(e.id, e.content, e.user_id) for e in new_entries]
        logger.info("Startup Keep sync: %s", stats)
        for entry_id, content, owner_id in to_score:
            asyncio.create_task(score_mood_background(entry_id, content, user_id=owner_id))
    except Exception:  # noqa: BLE001
        logger.exception("Startup Keep sync failed; continuing without it")


async def _scheduled_goodnotes_scan() -> None:
    """Scan the GoodNotes folder for newly-appended pages, then mood-score the
    new entries. Best-effort, mirroring `_startup_keep_sync`: any failure is
    logged and the schedule keeps running."""
    from services.ingestion import ingest_goodnotes, score_mood_background

    try:
        with Session(engine) as session:
            stats, new_entries = await ingest_goodnotes(session)
            to_score = [(e.id, e.content, e.user_id) for e in new_entries]
        logger.info("Scheduled GoodNotes scan: %s", stats)
        for entry_id, content, owner_id in to_score:
            asyncio.create_task(score_mood_background(entry_id, content, user_id=owner_id))
    except Exception:  # noqa: BLE001
        logger.exception("Scheduled GoodNotes scan failed; continuing without it")


def _start_goodnotes_scheduler(scan=_scheduled_goodnotes_scan):
    """Start an APScheduler interval job running the GoodNotes scan, when
    `GOODNOTES_SCAN_INTERVAL_MIN > 0`. Returns the started scheduler, or None
    when scheduling is disabled. The caller owns shutting it down."""
    interval = config.GOODNOTES_SCAN_INTERVAL_MIN
    if interval <= 0:
        return None

    # Imported here to keep import-time light when the scheduler is off.
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    scheduler = AsyncIOScheduler()
    scheduler.add_job(scan, "interval", minutes=interval, id="goodnotes_scan")
    scheduler.start()
    logger.info("GoodNotes scan scheduled every %d min", interval)
    return scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()  # before the first app log line below
    # Launch banner so the operator sees at a glance which DB is live.
    logger.info(
        "APP_MODE=%s → db=%s chroma=%s sync=%s",
        config.APP_MODE,
        config.DATABASE_URL,
        config.CHROMA_PERSIST_DIR,
        config.SYNC_ON_STARTUP,
    )
    _run_migrations()  # bring schema to head (Alembic; replaces create_all + ensure_schema)
    # Fire-and-forget so the server is usable immediately; new notes fold in
    # moments later rather than blocking boot on a full Keep sync.
    sync_task = asyncio.create_task(_startup_keep_sync()) if _sync_on_startup_enabled() else None
    try:
        goodnotes_scheduler = _start_goodnotes_scheduler()
    except Exception:  # noqa: BLE001
        logger.exception("GoodNotes scheduler failed to start; continuing without it")
        goodnotes_scheduler = None
    yield
    if goodnotes_scheduler is not None:
        goodnotes_scheduler.shutdown(wait=False)
    if sync_task and not sync_task.done():
        sync_task.cancel()


app = FastAPI(title="Journal Analytics API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[config.FRONTEND_URL],
    # The session cookie only rides along on SPA fetches when the browser is
    # told credentials are welcome (and the frontend passes `credentials:
    # "include"`). Requires an explicit origin above — "*" is refused.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Signed cookie holding the in-flight OAuth `state` between /auth/login and
# /auth/callback (Authlib's CSRF check). Handshake scaffolding only — the
# logged-in session is the separate httpOnly cookie from services/auth.py.
# `session_cookie` MUST differ from services.auth.SESSION_COOKIE: the
# middleware appends its own Set-Cookie (a deletion, once the state is
# consumed) after the route's, and under a shared name that deletes the
# login token in the very response that set it.
app.add_middleware(
    SessionMiddleware,
    secret_key=config.SESSION_SECRET,
    session_cookie="oauth_state",
    same_site="lax",
)

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(ingest.router, prefix="/ingest", tags=["ingest"])
app.include_router(synthesis.router, prefix="/stream", tags=["synthesis"])
app.include_router(chat.router, prefix="/stream", tags=["chat"])
app.include_router(search.router, prefix="/search", tags=["search"])
app.include_router(trends.router, prefix="/trends", tags=["trends"])
app.include_router(entries.router, prefix="/entries", tags=["entries"])
app.include_router(export.router, prefix="/export", tags=["export"])
app.include_router(keys.router, prefix="/keys", tags=["keys"])
app.include_router(usage.router, prefix="/usage", tags=["usage"])
