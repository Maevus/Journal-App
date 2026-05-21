from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel
from sqlmodel import Session

from models.journal import get_session
from services.ingestion import ingest_entries, score_mood_background

router = APIRouter()


class IngestRequest(BaseModel):
    entries: list[str]
    source: str = "text"  # stub: "google_keep" | "goodnotes" | "text"


@router.post("")
async def ingest(
    request: IngestRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    stats, new_entries = ingest_entries(request.entries, request.source, session)

    for entry in new_entries:
        background_tasks.add_task(score_mood_background, entry.id, entry.content)

    return stats
