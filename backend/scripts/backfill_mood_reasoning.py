"""
Backfill mood_reasoning for existing entries by re-scoring them via the LLM.

Until now the scorer discarded the reasoning, so entries created before that
have a mood_score but no reasoning. This re-scores every entry missing a
reasoning, writing back both score and reasoning.

Usage (from backend/):

    .venv/bin/python scripts/backfill_mood_reasoning.py          # real journal.db
    APP_MODE=demo .venv/bin/python scripts/backfill_mood_reasoning.py

Costs one LLM call per entry missing a reasoning, billed to each entry's owner
(F3). Safe to re-run — it only touches rows where mood_reasoning IS NULL. (The
demo DB is seeded with canned reasoning, so it normally has nothing to backfill.)

Ordering note: scoring an owner's entry spends on *their* budget. A free-tier
owner ($0 quota, no BYO key) has every scoring refused by the quota gate before
any API traffic, so those rows stay missing until the owner adds a key. Run this
after the owner's key is in place.
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

# Register every table in the shared metadata, as main.py does — otherwise
# JournalEntry.user_id's foreign key to `users` can't be resolved at commit
# time (SQLAlchemy: "could not find table 'users'").
import models.journal  # noqa: F401, E402
import models.user  # noqa: F401, E402
import models.keys  # noqa: F401, E402
import models.usage  # noqa: F401, E402

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
        todo = [(e.id, e.content, e.user_id) for e in _missing(session)]

    if not todo:
        print("Nothing to backfill — every entry already has a mood_reasoning.")
        return

    print(f"Backfilling {len(todo)} entries (one LLM call each)…")
    for n, (entry_id, content, user_id) in enumerate(todo, 1):
        # Scoring bills the entry's owner (F3). user_id=None rows are skipped by
        # score_mood_background (no owner to bill) and stay in the missing count.
        await score_mood_background(entry_id, content, user_id=user_id)
        print(f"  [{n}/{len(todo)}] entry {entry_id} (owner={user_id})")

    with Session(engine) as session:
        remaining = len(_missing(session))
    print(
        f"Done. {len(todo) - remaining} filled, {remaining} still missing "
        "(any scoring errors are logged above)."
    )


if __name__ == "__main__":
    asyncio.run(main())
