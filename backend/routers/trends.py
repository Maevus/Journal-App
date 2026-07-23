from collections import defaultdict
from datetime import date as date_cls
from datetime import datetime, time, timedelta

from fastapi import APIRouter, Depends
from sqlmodel import Session

from clock import utcnow_naive
from models.journal import JournalEntry, get_session, user_entries
from models.user import User
from services.auth import get_current_user

router = APIRouter()


@router.get("")
async def get_trends(
    days: int = 30,  # calendar window; 0 = all time
    source: str | None = None,  # "google_keep" | "goodnotes" | "text"; None = all
    start: date_cls | None = None,  # ISO "YYYY-MM-DD"; with `end`, overrides `days`
    end: date_cls | None = None,  # inclusive of the whole end day
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    query = (
        user_entries(user.id)
        .where(JournalEntry.mood_score.isnot(None))  # type: ignore[arg-type]
        .order_by(JournalEntry.timestamp.asc())
    )
    # An explicit start+end window wins over the `days` lookback; end is
    # inclusive, so cover the whole end day with a half-open [start, end+1day).
    custom_window = start is not None and end is not None
    if custom_window:
        query = query.where(JournalEntry.timestamp >= datetime.combine(start, time.min))
        query = query.where(
            JournalEntry.timestamp < datetime.combine(end, time.min) + timedelta(days=1)
        )
    elif days > 0:
        cutoff = utcnow_naive() - timedelta(days=days)
        query = query.where(JournalEntry.timestamp >= cutoff)
    if source:
        query = query.where(JournalEntry.source == source)
    entries = session.exec(query).all()

    daily: defaultdict[str, list[float]] = defaultdict(list)
    for entry in entries:
        daily[entry.timestamp.date().isoformat()].append(entry.mood_score)  # type: ignore[arg-type]

    trends = [
        {"date": date, "score": round(sum(scores) / len(scores), 4)}
        for date, scores in sorted(daily.items())
    ]

    # Return stub data when no scored entries exist yet. Skipped when a source
    # filter or an explicit date window is set — an empty chart is honest there,
    # a fake sinusoid is not.
    if not trends and source is None and not custom_window:
        stub_days = days or 30
        today = utcnow_naive().date()
        import random, math  # noqa: E401

        random.seed(42)
        trends = [
            {
                "date": (today - timedelta(days=stub_days - 1 - i)).isoformat(),
                "score": round(0.5 + 0.3 * math.sin(i / 3) + random.uniform(-0.05, 0.05), 4),
            }
            for i in range(stub_days)
        ]

    return {"trends": trends}
