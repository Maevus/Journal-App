"""Assign all unowned journal entries to a user, by email.

Interim bridge for pre-multi-user data (ADR 0003): rows created before the
`user_id` column — and rows from the user-less startup sync paths until their
owner first logs in — carry `user_id NULL`, which user-scoped reads will not
see. Run this once after logging in, from `backend/`:

    .venv/bin/python scripts/backfill_owner.py you@gmail.com

Idempotent: already-owned rows are never touched, and the Chroma pass just
re-stamps the same metadata. Both stores are covered: after assigning SQL
rows, every owned entry's vector gets a `user_id` metadata stamp, because the
filtered ANN query (slice 4) cannot see vectors without one. The canonical,
final backfill happens at the SQLite→Postgres migration; this script just
keeps a live single-user DB usable in the meantime.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from sqlmodel import Session, select  # noqa: E402

from models.journal import JournalEntry, engine  # noqa: E402
from models.user import User  # noqa: E402


def backfill_chroma(session: Session) -> None:
    """Stamp every owned entry's vector with its owner, from SQL (the source of
    truth for ownership). Covers vectors embedded before slice 4 — and rows a
    previous run of this script assigned while Chroma wasn't backfilled yet.
    Only ids present in both stores are touched; update() rewrites metadata
    without re-embedding."""
    from services.embeddings import _get_collection

    owner_by_id = {
        str(e.id): e.user_id
        for e in session.exec(
            select(JournalEntry).where(JournalEntry.user_id != None)  # noqa: E711
        ).all()
    }
    collection = _get_collection()
    existing = set(collection.get(include=[])["ids"])  # ids only — skip embeddings/documents
    ids = sorted(owner_by_id.keys() & existing, key=int)
    if not ids:
        print("Chroma: no vectors to stamp")
        return
    collection.update(ids=ids, metadatas=[{"user_id": owner_by_id[i]} for i in ids])
    print(f"Chroma: stamped user_id onto {len(ids)} vectors")


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
        backfill_chroma(session)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python scripts/backfill_owner.py <email>")
    main(sys.argv[1])
