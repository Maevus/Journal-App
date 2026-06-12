from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session

from models.journal import get_session
from services.ingestion import (
    ingest_entries,
    ingest_goodnotes,
    ingest_keep,
    score_mood_background,
)

router = APIRouter()


class IngestRequest(BaseModel):
    entries: list[str]
    source: str = "text"


@router.post("")
async def ingest(
    background_tasks: BackgroundTasks,
    source: Optional[str] = Query(
        default=None,
        description="Automated source: 'keep', 'goodnotes', or 'all'. "
        "Omit to post entries directly via request body.",
    ),
    request: Optional[IngestRequest] = None,
    session: Session = Depends(get_session),
):
    if source == "keep":
        stats, new_entries = await ingest_keep(session)
    elif source == "goodnotes":
        stats, new_entries = await ingest_goodnotes(session)
    elif source == "all":
        keep_stats, keep_entries = await ingest_keep(session)
        gn_stats, gn_entries = await ingest_goodnotes(session)
        stats = {
            "ingested": keep_stats["ingested"] + gn_stats["ingested"],
            "updated": keep_stats["updated"] + gn_stats["updated"],
            "skipped": keep_stats["skipped"] + gn_stats["skipped"],
        }
        new_entries = keep_entries + gn_entries
    else:
        if request is None:
            raise HTTPException(
                status_code=422,
                detail="Request body required when source is not 'keep', 'goodnotes', or 'all'.",
            )
        stats, new_entries = ingest_entries(request.entries, request.source, session)

    for entry in new_entries:
        background_tasks.add_task(score_mood_background, entry.id, entry.content)

    return stats
