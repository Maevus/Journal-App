"""
Entry ingestion: parse, deduplicate (content hash), persist, and
asynchronously score mood via the LLM.
"""
import hashlib
import logging
from datetime import datetime
from typing import List, Tuple

from pydantic import BaseModel
from sqlmodel import Session, select

from models.journal import JournalEntry, engine
from services.embeddings import embed_and_store

logger = logging.getLogger(__name__)


def compute_hash(content: str) -> str:
    return hashlib.sha256(content.strip().encode("utf-8")).hexdigest()


def ingest_entries(
    raw_entries: List[str],
    source: str,
    session: Session,
) -> Tuple[dict, List[JournalEntry]]:
    """
    Persist new entries, skipping duplicates by content hash.
    Returns (stats_dict, list_of_new_JournalEntry_objects).
    """
    new_entries: List[JournalEntry] = []
    skipped = 0

    for raw in raw_entries:
        content = raw.strip()
        if not content:
            skipped += 1
            continue

        content_hash = compute_hash(content)
        existing = session.exec(
            select(JournalEntry).where(JournalEntry.content_hash == content_hash)
        ).first()

        if existing:
            skipped += 1
            continue

        entry = JournalEntry(
            source=source,
            content=content,
            timestamp=datetime.utcnow(),
            content_hash=content_hash,
        )
        session.add(entry)
        session.commit()
        session.refresh(entry)

        embed_and_store(entry.id, content)
        new_entries.append(entry)

    return {"ingested": len(new_entries), "skipped": skipped}, new_entries


class _MoodScore(BaseModel):
    score: float  # 0.0 = very negative, 1.0 = very positive
    reasoning: str


async def score_mood_background(entry_id: int, content: str) -> None:
    """
    Background task: call the LLM for a mood score and write it back to the DB.
    Failures are silently swallowed — scoring is best-effort.
    """
    try:
        from services.llm_client import structured_output

        result = await structured_output(
            prompt=f"Analyse the mood of this journal entry:\n\n{content[:600]}",
            output_schema=_MoodScore,
            system=(
                "You are an emotional-intelligence expert. "
                "Score the mood of a journal entry on a scale from 0.0 (very negative) "
                "to 1.0 (very positive). Return a precise float and a one-sentence reasoning."
            ),
            max_tokens=128,
        )

        with Session(engine) as session:
            entry = session.get(JournalEntry, entry_id)
            if entry:
                entry.mood_score = max(0.0, min(1.0, result.score))
                session.commit()

    except Exception as exc:  # noqa: BLE001
        logger.warning("Mood scoring failed for entry %d: %s", entry_id, exc)
