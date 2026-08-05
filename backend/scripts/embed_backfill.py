"""Re-embed every journal entry into the pgvector store.

Iterates all JournalEntry rows, embeds each with fastembed, upserts into
entry_embeddings. Local and free (no API). Idempotent — re-running overwrites.
Run migrations first. From backend/ with DATABASE_URL pointing at Postgres:

    DATABASE_URL=postgresql+psycopg://…  python scripts/embed_backfill.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from sqlmodel import Session, select  # noqa: E402

import config  # noqa: E402
from models.journal import JournalEntry, engine  # noqa: E402
from services.embeddings import embed_and_store  # noqa: E402


def main() -> None:
    if not config.DATABASE_URL.startswith(("postgresql", "postgres")):
        sys.exit(
            f"DATABASE_URL must be Postgres, got {config.DATABASE_URL!r}. "
            "The pgvector store only exists on Postgres."
        )
    with Session(engine) as session:
        entries = session.exec(select(JournalEntry)).all()

    print(f"Embedding {len(entries)} entries into entry_embeddings…")
    for i, entry in enumerate(entries, 1):
        embed_and_store(entry.id, entry.content, user_id=entry.user_id)
        if i % 100 == 0 or i == len(entries):
            print(f"  {i}/{len(entries)}")
    print("Done.")


if __name__ == "__main__":
    main()
