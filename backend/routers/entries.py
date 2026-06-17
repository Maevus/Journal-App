from datetime import date as date_type
from datetime import datetime, time, timedelta

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from models.journal import JournalEntry, get_session

router = APIRouter()


@router.get("")
async def get_entries(
    date: date_type,
    source: str | None = None,  # "google_keep" | "goodnotes" | "text"; None = all
    session: Session = Depends(get_session),
):
    """Return all journal entries written on a specific calendar day (UTC)."""
    start = datetime.combine(date, time.min)
    end = start + timedelta(days=1)

    query = (
        select(JournalEntry)
        .where(JournalEntry.timestamp >= start, JournalEntry.timestamp < end)
        .order_by(JournalEntry.timestamp.asc())
    )
    if source:
        query = query.where(JournalEntry.source == source)
    entries = session.exec(query).all()

    return {
        "entries": [
            {
                "id": e.id,
                "content": e.content,
                "source": e.source,
                "timestamp": e.timestamp.isoformat(),
                "imported_at": e.imported_at.isoformat() if e.imported_at else None,
                "mood_score": e.mood_score,
                "mood_reasoning": e.mood_reasoning,
                "external_id": e.external_id,
            }
            for e in entries
        ]
    }
