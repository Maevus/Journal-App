import csv
import io
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlmodel import Session

from models.journal import JournalEntry, get_session, user_entries
from models.user import User
from services.auth import get_current_user

router = APIRouter()

# Fields exported per entry, in column order.
EXPORT_FIELDS = [
    "id",
    "timestamp",
    "source",
    "content",
    "mood_score",
    "mood_reasoning",
    "external_id",
]


def _entry_dict(entry: JournalEntry) -> dict:
    return {
        "id": entry.id,
        "timestamp": entry.timestamp.isoformat(),
        "source": entry.source,
        "content": entry.content,
        "mood_score": entry.mood_score,
        "mood_reasoning": entry.mood_reasoning,
        "external_id": entry.external_id,
    }


@router.get("")
async def export_entries(
    format: str = "csv",  # "csv" | "json"
    source: str | None = None,  # "google_keep" | "goodnotes" | "text"; None = all
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Export the user's journal entries (optionally filtered by source) for
    download as CSV or JSON, ordered by timestamp ascending."""
    if format not in ("csv", "json"):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format {format!r}; expected 'csv' or 'json'.",
        )

    query = user_entries(user.id).order_by(JournalEntry.timestamp.asc())
    if source:
        query = query.where(JournalEntry.source == source)
    entries = session.exec(query).all()

    rows = [_entry_dict(e) for e in entries]

    if format == "json":
        return Response(
            content=json.dumps(rows),
            media_type="application/json",
            headers={
                "Content-Disposition": 'attachment; filename="journal_export.json"'
            },
        )

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="journal_export.csv"'
        },
    )
