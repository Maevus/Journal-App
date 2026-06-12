import asyncio
import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, SQLModel

load_dotenv()

# Import models so SQLModel registers their tables before create_all
import models.journal  # noqa: F401, E402
from models.journal import engine
from routers import entries, ingest, search, synthesis, trends

logger = logging.getLogger(__name__)


def _sync_on_startup_enabled() -> bool:
    """Sync Keep when the app opens, unless explicitly disabled. Off is useful
    under `uvicorn --reload`, which restarts (and would re-sync) on every save."""
    if os.getenv("SYNC_ON_STARTUP", "1").lower() in ("0", "false", "no"):
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    SQLModel.metadata.create_all(engine)
    # Fire-and-forget so the server is usable immediately; new notes fold in
    # moments later rather than blocking boot on a full Keep sync.
    sync_task = asyncio.create_task(_startup_keep_sync()) if _sync_on_startup_enabled() else None
    yield
    if sync_task and not sync_task.done():
        sync_task.cancel()


app = FastAPI(title="Journal Analytics API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ingest.router, prefix="/ingest", tags=["ingest"])
app.include_router(synthesis.router, prefix="/stream", tags=["synthesis"])
app.include_router(search.router, prefix="/search", tags=["search"])
app.include_router(trends.router, prefix="/trends", tags=["trends"])
app.include_router(entries.router, prefix="/entries", tags=["entries"])
