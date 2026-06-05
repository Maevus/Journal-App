"""
Entry ingestion: parse, deduplicate (content hash), persist, and
asynchronously score mood via the LLM.

Sources:
  - text        plain entries posted directly to /ingest
  - google_keep fetched via gkeepapi (GOOGLE_EMAIL + GOOGLE_APP_MASTER_TOKEN)
  - goodnotes   scanned from iCloud folder (GOODNOTES_ICLOUD_PATH)
"""
import asyncio
import base64
import hashlib
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import anthropic
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


# ---------------------------------------------------------------------------
# OCR helper — Claude vision (handles images and image-only PDFs)
# ---------------------------------------------------------------------------

_OCR_MODEL = "claude-3-5-sonnet-20241022"
_IMAGE_MEDIA_TYPES: Dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


async def _ocr_with_claude(file_bytes: bytes, media_type: str) -> str:
    """Transcribe handwritten or printed content via Claude vision."""
    client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    encoded = base64.standard_b64encode(file_bytes).decode()

    if media_type == "application/pdf":
        content_block: dict = {
            "type": "document",
            "source": {"type": "base64", "media_type": media_type, "data": encoded},
        }
    else:
        content_block = {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": encoded},
        }

    response = await client.messages.create(
        model=_OCR_MODEL,
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": [
                    content_block,
                    {
                        "type": "text",
                        "text": (
                            "Transcribe all handwritten or printed text from this journal page. "
                            "Output only the transcribed text, no commentary."
                        ),
                    },
                ],
            }
        ],
    )
    for block in response.content:
        if hasattr(block, "text"):
            return block.text
    return ""


def _extract_pdf_text(path: Path) -> str:
    """Extract the text layer from a PDF; returns empty string for scanned/image-only pages."""
    import pypdf

    parts: List[str] = []
    with open(path, "rb") as fh:
        reader = pypdf.PdfReader(fh)
        for page in reader.pages:
            parts.append(page.extract_text() or "")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Google Keep ingester
# ---------------------------------------------------------------------------


async def ingest_keep(session: Session) -> Tuple[dict, List[JournalEntry]]:
    """Fetch all Google Keep notes and persist new ones (idempotent via content hash)."""
    import gkeepapi

    email = os.environ["GOOGLE_EMAIL"]
    master_token = os.environ["GOOGLE_APP_MASTER_TOKEN"]

    def _fetch() -> List[str]:
        keep = gkeepapi.Keep()
        keep.authenticate(email, master_token)
        keep.sync()
        texts: List[str] = []
        for note in keep.all():
            parts: List[str] = []
            if getattr(note, "title", None):
                parts.append(note.title)
            if getattr(note, "text", None):
                parts.append(note.text)
            combined = "\n".join(parts)
            if combined.strip():
                texts.append(combined)
        return texts

    texts = await asyncio.get_running_loop().run_in_executor(None, _fetch)
    logger.info("Google Keep: fetched %d notes", len(texts))
    return ingest_entries(texts, "google_keep", session)


# ---------------------------------------------------------------------------
# GoodNotes (iCloud folder) ingester
# ---------------------------------------------------------------------------


async def _ensure_local(path: Path) -> Optional[Path]:
    """
    Resolve an iCloud eviction placeholder (.{name}.icloud) to the real local file,
    triggering `brctl download` if needed. Non-iCloud files are returned unchanged.
    Returns None if the file cannot be made available locally within 30 s.
    """
    if not (path.name.startswith(".") and path.suffix == ".icloud"):
        return path

    # .Journal.pdf.icloud → Journal.pdf
    real_name = path.stem.lstrip(".")
    real_path = path.parent / real_name

    if real_path.exists():
        return real_path

    proc = await asyncio.create_subprocess_exec(
        "brctl", "download", str(path),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()

    for _ in range(30):
        if real_path.exists():
            return real_path
        await asyncio.sleep(1)

    logger.warning("iCloud download timed out for %s", path.name)
    return None


async def ingest_goodnotes(session: Session) -> Tuple[dict, List[JournalEntry]]:
    """Scan the iCloud GoodNotes folder and persist new entries (idempotent via content hash)."""
    raw_path = os.environ["GOODNOTES_ICLOUD_PATH"]
    folder = Path(os.path.expanduser(raw_path))

    if not folder.exists():
        logger.warning("GoodNotes iCloud path does not exist: %s", folder)
        return {"ingested": 0, "skipped": 0}, []

    texts: List[str] = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue

        resolved = await _ensure_local(path)
        if resolved is None:
            continue

        suffix = resolved.suffix.lower()
        if suffix == ".pdf":
            text = _extract_pdf_text(resolved)
        elif suffix in _IMAGE_MEDIA_TYPES:
            text = await _ocr_with_claude(resolved.read_bytes(), _IMAGE_MEDIA_TYPES[suffix])
        else:
            continue

        if text.strip():
            texts.append(text)

    logger.info("GoodNotes: extracted text from %d file(s) in %s", len(texts), folder)
    return ingest_entries(texts, "goodnotes", session)
