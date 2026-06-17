"""
Backfill mood_reasoning for existing entries by re-scoring them via the LLM.

Until now the scorer discarded the reasoning, so entries created before that
have a mood_score but no reasoning. This re-scores every entry missing a
reasoning, writing back both score and reasoning.

Usage (from backend/):

    .venv/bin/python scripts/backfill_mood_reasoning.py          # real journal.db
    APP_MODE=demo .venv/bin/python scripts/backfill_mood_reasoning.py

Costs one LLM call per entry missing a reasoning. Safe to re-run — it only
touches rows where mood_reasoning IS NULL. (The demo DB is seeded with canned
reasoning, so it normally has nothing to backfill.)
"""
import asyncio
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
os.chdir(BACKEND_DIR)
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from sqlmodel import Session, SQLModel, select  # noqa: E402

from models.journal import JournalEntry, engine, ensure_schema  # noqa: E402
from services.ingestion import score_mood_background  # noqa: E402


def _missing(session: Session) -> list[JournalEntry]:
    return list(
        session.exec(
            select(JournalEntry).where(JournalEntry.mood_reasoning.is_(None))  # type: ignore[union-attr]
        ).all()
    )


async def main() -> None:
    SQLModel.metadata.create_all(engine)
    ensure_schema()  # make sure the mood_reasoning column exists first

    with Session(engine) as session:
        todo = [(e.id, e.content) for e in _missing(session)]

    if not todo:
        print("Nothing to backfill — every entry already has a mood_reasoning.")
        return

    print(f"Backfilling {len(todo)} entries (one LLM call each)…")
    for n, (entry_id, content) in enumerate(todo, 1):
        await score_mood_background(entry_id, content)  # writes score + reasoning
        print(f"  [{n}/{len(todo)}] entry {entry_id}")

    with Session(engine) as session:
        remaining = len(_missing(session))
    print(
        f"Done. {len(todo) - remaining} filled, {remaining} still missing "
        "(any scoring errors are logged above)."
    )


if __name__ == "__main__":
    asyncio.run(main())
