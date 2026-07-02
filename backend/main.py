import asyncio
import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, SQLModel
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()

# Import after load_dotenv so APP_MODE / DB paths reflect .env, not just the shell.
import config  # noqa: E402

# Import models so SQLModel registers their tables before create_all
import models.journal  # noqa: F401, E402
import models.user  # noqa: F401, E402
from models.journal import engine
from routers import auth, chat, entries, export, ingest, search, synthesis, trends

logger = logging.getLogger(__name__)


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
            to_score = [(e.id, e.content) for e in new_entries]
        logger.info("Startup Keep sync: %s", stats)
        for entry_id, content in to_score:
            asyncio.create_task(score_mood_background(entry_id, content))
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
            to_score = [(e.id, e.content) for e in new_entries]
        logger.info("Scheduled GoodNotes scan: %s", stats)
        for entry_id, content in to_score:
            asyncio.create_task(score_mood_background(entry_id, content))
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
    # Plain print (not logger.info, which the default uvicorn config swallows):
    # this is a launch banner so the operator can see at a glance which DB is live.
    # flush=True so it appears immediately even when stdout is redirected to a file.
    print(
        f"APP_MODE={config.APP_MODE} → db={config.DATABASE_URL} "
        f"chroma={config.CHROMA_PERSIST_DIR} sync={config.SYNC_ON_STARTUP}",
        flush=True,
    )
    SQLModel.metadata.create_all(engine)
    models.journal.ensure_schema()  # add columns missing from a pre-existing DB
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
app.add_middleware(SessionMiddleware, secret_key=config.SESSION_SECRET, same_site="lax")

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(ingest.router, prefix="/ingest", tags=["ingest"])
app.include_router(synthesis.router, prefix="/stream", tags=["synthesis"])
app.include_router(chat.router, prefix="/stream", tags=["chat"])
app.include_router(search.router, prefix="/search", tags=["search"])
app.include_router(trends.router, prefix="/trends", tags=["trends"])
app.include_router(entries.router, prefix="/entries", tags=["entries"])
app.include_router(export.router, prefix="/export", tags=["export"])
