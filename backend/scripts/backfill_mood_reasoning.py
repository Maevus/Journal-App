"""Backfill mood_reasoning for entries that have a mood_score but no reasoning.

Usage (from backend/):

    .venv/bin/python scripts/backfill_mood_reasoning.py          # real journal.db
    APP_MODE=demo .venv/bin/python scripts/backfill_mood_reasoning.py

One LLM call per entry, billed to its owner (F3). Safe to re-run — only touches
rows where mood_reasoning IS NULL. Free-tier owners with no BYO key have the
quota gate refuse every call, so their rows stay missing until they add a key.
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

from sqlmodel import Session, select  # noqa: E402

# Register every table in the shared metadata (like main.py) — otherwise the
# FK from JournalEntry.user_id to `users` can't resolve at commit time.
import models.journal  # noqa: F401, E402
import models.user  # noqa: F401, E402
import models.keys  # noqa: F401, E402
import models.usage  # noqa: F401, E402

from models.journal import JournalEntry, engine  # noqa: E402
from services.ingestion import score_mood_background  # noqa: E402


def _missing(session: Session) -> list[JournalEntry]:
    return list(
        session.exec(
            select(JournalEntry).where(JournalEntry.mood_reasoning.is_(None))  # type: ignore[union-attr]
        ).all()
    )


async def main() -> None:
    # Schema is Alembic-managed now (the app self-migrates on startup); this
    # backfill assumes the DB is already at head.
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
