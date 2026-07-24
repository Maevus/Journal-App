from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlmodel import Session

from models.journal import get_session
from models.user import User
from services.auth import get_current_user, is_demo_user
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
    force: bool = Query(
        default=False,
        description="GoodNotes only: re-OCR every page even if already ingested. "
        "Off by default so re-exporting the growing PDF only OCRs new pages.",
    ),
    request: Optional[IngestRequest] = None,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    # The demo is a read-only showcase: a signed-out visitor must not write to the
    # shared demo journal (chat + search stay open, bounded by the demo budget).
    if is_demo_user(user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "The demo journal is read-only — sign in to add entries."
        )
    if source == "keep":
        stats, new_entries = await ingest_keep(session)
    elif source == "goodnotes":
        stats, new_entries = await ingest_goodnotes(session, force=force)
    elif source == "all":
        keep_stats, keep_entries = await ingest_keep(session)
        gn_stats, gn_entries = await ingest_goodnotes(session, force=force)
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
        # Direct text posts belong to whoever posted them. The keep/goodnotes
        # branches above resolve their own owner (the env-configured GOOGLE_EMAIL
        # account) — the connectors are single-account legacy, and the user
        # *triggering* a sync isn't necessarily the account being synced.
        stats, new_entries = ingest_entries(
            request.entries, request.source, session, user_id=user.id
        )

    for entry in new_entries:
        background_tasks.add_task(score_mood_background, entry.id, entry.content, user_id=entry.user_id)

    return stats
