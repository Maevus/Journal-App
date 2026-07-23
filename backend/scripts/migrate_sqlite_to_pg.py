"""One-time data copy: SQLite journal.db → Postgres (ADR 0003, the engine move).

Copies every row of every table from the legacy SQLite DB into a freshly-migrated
Postgres DB, **preserving primary keys** so the Chroma vectors (keyed by
`str(entry.id)`) stay aligned without re-embedding. The SQLite file is opened
read-only and never written, so it remains an intact rollback.

Run order (from backend/, venv active):

  1. docker compose up -d db                         # Postgres on :5432
  2. DATABASE_URL=postgresql+psycopg://…  alembic upgrade head   # empty schema
  3. python scripts/backfill_owner.py you@gmail.com  # on the SOURCE sqlite DB,
                                                     # so no NULL user_id remains
  4. DATABASE_URL=postgresql+psycopg://…  python scripts/migrate_sqlite_to_pg.py

`DATABASE_URL` (the target) must be Postgres; `--source` defaults to the real
`sqlite:///./journal.db`. Refuses to run if the target already holds data.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from sqlalchemy import func, text  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine, select  # noqa: E402

import config  # noqa: E402

# Import all models so their tables register on SQLModel.metadata.
from models.journal import JournalEntry  # noqa: E402
from models.keys import UserApiKey  # noqa: E402
from models.usage import LLMUsage  # noqa: E402
from models.user import User, UserSession  # noqa: E402

# FK-safe copy order: parents (users) before children (everything FK-ing users).
# llmusage has no FK but is harmless last.
COPY_ORDER = [User, UserSession, JournalEntry, UserApiKey, LLMUsage]


def _count(session: Session, model) -> int:
    return session.exec(select(func.count()).select_from(model)).one()


def main(source_url: str) -> None:
    target_url = config.DATABASE_URL
    if not target_url.startswith(("postgresql", "postgres")):
        sys.exit(
            f"Target DATABASE_URL must be Postgres, got {target_url!r}. "
            "Set DATABASE_URL=postgresql+psycopg://… before running."
        )
    if source_url == target_url:
        sys.exit("Source and target are the same DB — nothing to migrate.")

    source_engine = create_engine(source_url)
    target_engine = create_engine(target_url)

    # The target schema must exist (alembic upgrade head) and be empty.
    with Session(target_engine) as tgt:
        try:
            existing = {m.__tablename__: _count(tgt, m) for m in COPY_ORDER}
        except Exception as exc:  # noqa: BLE001
            sys.exit(
                f"Target isn't ready ({exc}). Run `alembic upgrade head` against "
                "the Postgres DATABASE_URL first."
            )
        if any(existing.values()):
            sys.exit(
                "Target already contains data "
                f"({', '.join(f'{k}={v}' for k, v in existing.items() if v)}). "
                "Refusing to copy into a non-empty DB — drop/recreate it first "
                "(`docker compose down -v && docker compose up -d db`, then "
                "`alembic upgrade head`)."
            )

    # JournalEntry.user_id is NOT NULL in the target; a stray unowned source row
    # would fail the copy. Catch it early with a clear message.
    with Session(source_engine) as src:
        unowned = src.exec(
            select(func.count()).select_from(JournalEntry).where(JournalEntry.user_id == None)  # noqa: E711
        ).one()
    if unowned:
        sys.exit(
            f"{unowned} journal entries have no owner (user_id NULL). Run "
            "`python scripts/backfill_owner.py <email>` on the source DB first."
        )

    copied: dict[str, int] = {}
    with Session(source_engine) as src, Session(target_engine) as tgt:
        for model in COPY_ORDER:
            rows = src.exec(select(model)).all()
            for row in rows:
                # model_dump() carries every column including the primary key, so
                # ids are preserved verbatim (Chroma alignment depends on this).
                tgt.add(model(**row.model_dump()))
            tgt.commit()
            copied[model.__tablename__] = len(rows)
            print(f"  {model.__tablename__}: {len(rows)}")

        # Explicit-id inserts don't advance Postgres' serial sequences; nextval
        # would collide with an existing id. Bump each to MAX(id).
        for model in COPY_ORDER:
            tbl = model.__tablename__
            tgt.exec(  # type: ignore[call-overload]
                text(
                    f"SELECT setval(pg_get_serial_sequence('{tbl}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {tbl}), 1), "
                    f"(SELECT MAX(id) FROM {tbl}) IS NOT NULL)"
                )
            )
        tgt.commit()

    # Verify parity.
    with Session(source_engine) as src, Session(target_engine) as tgt:
        for model in COPY_ORDER:
            s, t = _count(src, model), _count(tgt, model)
            status = "OK" if s == t else "MISMATCH"
            print(f"  {model.__tablename__}: source={s} target={t} [{status}]")
            if s != t:
                sys.exit(f"Row-count mismatch on {model.__tablename__} — aborting.")

    total = sum(copied.values())
    print(f"\nCopied {total} rows across {len(COPY_ORDER)} tables into Postgres.")
    print("Chroma is unchanged — vectors stay aligned via preserved entry ids.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Copy SQLite journal.db → Postgres.")
    parser.add_argument(
        "--source",
        default="sqlite:///./journal.db",
        help="Source SQLAlchemy URL (default: the real sqlite:///./journal.db).",
    )
    main(parser.parse_args().source)
