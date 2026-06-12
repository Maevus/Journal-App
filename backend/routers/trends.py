from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from models.journal import JournalEntry, get_session

router = APIRouter()


@router.get("")
async def get_trends(
    days: int = 30,  # calendar window; 0 = all time
    session: Session = Depends(get_session),
):
    query = (
        select(JournalEntry)
        .where(JournalEntry.mood_score.isnot(None))  # type: ignore[arg-type]
        .order_by(JournalEntry.timestamp.asc())
    )
    if days > 0:
        cutoff = datetime.utcnow() - timedelta(days=days)
        query = query.where(JournalEntry.timestamp >= cutoff)
    entries = session.exec(query).all()

    daily: defaultdict[str, list[float]] = defaultdict(list)
    for entry in entries:
        daily[entry.timestamp.date().isoformat()].append(entry.mood_score)  # type: ignore[arg-type]

    trends = [
        {"date": date, "score": round(sum(scores) / len(scores), 4)}
        for date, scores in sorted(daily.items())
    ]

    # Return stub data when no scored entries exist yet
    if not trends:
        stub_days = days or 30
        today = datetime.utcnow().date()
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
