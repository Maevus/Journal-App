"""Assign all unowned journal entries (`user_id NULL`) to a user, by email (ADR 0003).

User-scoped reads skip NULL-owner rows, so run this once after logging in,
from `backend/`:

    .venv/bin/python scripts/backfill_owner.py you@gmail.com

Idempotent — already-owned rows are untouched. Vector ownership is handled
separately by scripts/embed_backfill.py, not here.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from sqlmodel import Session, select  # noqa: E402

from models.journal import JournalEntry, engine  # noqa: E402
from models.user import User  # noqa: E402


def main(email: str) -> None:
    with Session(engine) as session:
        user = session.exec(select(User).where(User.email == email)).first()
        if user is None:
            sys.exit(
                f"No user with email {email!r} — sign in with Google once first, "
                "so the account exists to assign the entries to."
            )
        unowned = session.exec(
            select(JournalEntry).where(JournalEntry.user_id == None)  # noqa: E711
        ).all()
        for entry in unowned:
            entry.user_id = user.id
            session.add(entry)
        session.commit()
        print(f"Backfilled {len(unowned)} unowned entries → {email} (user id {user.id})")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python scripts/backfill_owner.py <email>")
    main(sys.argv[1])
