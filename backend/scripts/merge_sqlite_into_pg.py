"""Additive reconcile: copy entries SQLite has and Postgres doesn't.

`migrate_sqlite_to_pg.py` is the clean one-shot copy and refuses a non-empty
target. This is for the messier case that actually happened — both databases
were written to for a while, so each holds entries the other lacks and neither
may be dropped.

Matches on (user, content_hash), the same key as the app's dedup constraint, and
inserts only what is missing. Rows get fresh Postgres ids; embeddings are keyed
by id, so run scripts/embed_backfill.py afterwards. Nothing is updated or
deleted, so re-running is a no-op.

Users are matched by EMAIL — ids are not comparable across the two databases.

Usage (from backend/):

    DATABASE_URL=postgresql+psycopg://journal:journal@127.0.0.1:5432/journal \
        .venv/bin/python scripts/merge_sqlite_into_pg.py [--source sqlite:///./journal.db] [--apply]

Defaults to a dry run; --apply performs the insert.
"""
import argparse
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
os.chdir(BACKEND_DIR)
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from sqlmodel import Session, create_engine, select  # noqa: E402

import models.keys  # noqa: F401, E402
import models.ocr  # noqa: F401, E402
import models.usage  # noqa: F401, E402
from models.journal import JournalEntry  # noqa: E402
from models.ocr import BlankPage  # noqa: E402
from models.user import User  # noqa: E402


def main(source_url: str, target_url: str, apply: bool) -> int:
    if not target_url.startswith(("postgresql", "postgres")):
        sys.exit(f"Target must be Postgres, got {target_url!r}")

    src_engine = create_engine(source_url)
    tgt_engine = create_engine(target_url)

    with Session(src_engine) as src, Session(tgt_engine) as tgt:
        # Email is the only identifier meaningful in both databases.
        tgt_user_by_email = {u.email: u.id for u in tgt.exec(select(User)).all()}
        src_users = {u.id: u.email for u in src.exec(select(User)).all()}

        missing_users = [e for e in src_users.values() if e not in tgt_user_by_email]
        if missing_users:
            sys.exit(f"Users exist in source but not target: {missing_users}. Create them first.")

        existing = {
            (row.user_id, row.content_hash) for row in tgt.exec(select(JournalEntry)).all()
        }

        to_copy = []
        for row in src.exec(select(JournalEntry)).all():
            tgt_uid = tgt_user_by_email[src_users[row.user_id]]
            if (tgt_uid, row.content_hash) in existing:
                continue
            to_copy.append((tgt_uid, row))

        print(f"source entries : {len(src.exec(select(JournalEntry)).all())}")
        print(f"target entries : {len(existing)}")
        print(f"to copy        : {len(to_copy)}")
        for uid, row in to_copy[:5]:
            print(f"   user={uid} {row.source} {row.timestamp.date()} {row.content[:50]!r}")
        if len(to_copy) > 5:
            print(f"   ... and {len(to_copy) - 5} more")

        # Tombstones are keyed (source, external_id) with no user dimension.
        tgt_tombstones = {
            (b.source, b.external_id) for b in tgt.exec(select(BlankPage)).all()
        }
        blanks = [
            b for b in src.exec(select(BlankPage)).all()
            if (b.source, b.external_id) not in tgt_tombstones
        ]
        print(f"tombstones     : {len(blanks)} to copy")

        if not apply:
            print("\nDry run — pass --apply to write.")
            return 0

        for tgt_uid, row in to_copy:
            data = row.model_dump()
            data.pop("id")  # fresh Postgres id; embeddings are rebuilt after
            data["user_id"] = tgt_uid
            tgt.add(JournalEntry(**data))
        for b in blanks:
            data = b.model_dump()
            data.pop("id")
            tgt.add(BlankPage(**data))
        tgt.commit()

        total = len(tgt.exec(select(JournalEntry)).all())
        print(f"\nCopied {len(to_copy)} entries + {len(blanks)} tombstones.")
        print(f"Target now holds {total} entries.")
        print("Run scripts/embed_backfill.py — the new rows have no embeddings yet.")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", default="sqlite:///./journal.db")
    p.add_argument("--apply", action="store_true", help="write (default is a dry run)")
    args = p.parse_args()
    raise SystemExit(main(args.source, os.environ["DATABASE_URL"], args.apply))
