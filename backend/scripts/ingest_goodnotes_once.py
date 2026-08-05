"""
Run one GoodNotes folder scan now, without going through the HTTP route.

Same work as the scheduled scan in `main._scheduled_goodnotes_scan`: OCR the
pages of the exported PDF that aren't in the DB yet, ingest them, then mood-score
what landed. Going direct rather than via POST /ingest?source=goodnotes skips the
session-cookie auth and the ingest rate limit (5/min), neither of which means
anything for an operator-run backfill.

Usage (from backend/):

    .venv/bin/python scripts/ingest_goodnotes_once.py
    .venv/bin/python scripts/ingest_goodnotes_once.py --force   # re-OCR every page

Costs one Claude-vision call per *new* page plus one scoring call per ingested
entry, billed to the connector's owner (GOOGLE_EMAIL's user). Already-ingested
pages are skipped before any paid call, so re-running is cheap — with one
exception: pages that OCR'd blank record no row, so they are re-transcribed on
every run.

`--force` re-OCRs the whole PDF. On a year-long notebook that is the entire
backup at full price; it exists for repairing bad transcriptions, not routine use.
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

from sqlmodel import Session  # noqa: E402

# Register every table in the shared metadata, as main.py does — otherwise
# JournalEntry.user_id's foreign key to `users` can't be resolved at commit time.
import models.journal  # noqa: F401, E402
import models.user  # noqa: F401, E402
import models.keys  # noqa: F401, E402
import models.usage  # noqa: F401, E402

from models.journal import engine  # noqa: E402
from services.ingestion import ingest_goodnotes, score_mood_background  # noqa: E402


async def main() -> int:
    force = "--force" in sys.argv

    print(f"GOODNOTES_PATH = {os.getenv('GOODNOTES_PATH')}")
    print(f"DATABASE_URL   = {os.getenv('DATABASE_URL', 'sqlite:///./journal.db')}")
    print(f"force          = {force}\n")
    print("Scanning (each new page is one Claude-vision call)...")

    with Session(engine) as session:
        stats, new_entries = await ingest_goodnotes(session, force=force)
        # Read what scoring needs before the session closes.
        to_score = [(e.id, e.content, e.user_id) for e in new_entries]

    print(f"\nIngest: {stats}")

    # A stale GOODNOTES_PATH returns zeros with this key rather than looking
    # like an empty inbox — surface it as a non-zero exit, not a quiet success.
    if "error" in stats:
        print(f"\nFAILED: {stats['error']}", file=sys.stderr)
        return 1

    if to_score:
        print(f"Mood-scoring {len(to_score)} entr(ies)...")
        await asyncio.gather(
            *(
                score_mood_background(entry_id, content, user_id=owner_id)
                for entry_id, content, owner_id in to_score
            )
        )
        print("Scoring done.")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
