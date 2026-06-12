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
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple, Union

import anthropic
from pydantic import BaseModel
from sqlmodel import Session, select

from models.journal import JournalEntry, engine
from services.embeddings import embed_and_store

logger = logging.getLogger(__name__)


class RawEntry(NamedTuple):
    """A piece of content to ingest, with the date it was authored at the source.

    `timestamp` is the note's real authored time (naive UTC). When None, the
    ingest path falls back to "now" — used by sources that carry no date.
    `external_id` is the note's ID at its source (e.g. Keep note id), enabling
    deep links back to the original.
    """

    content: str
    timestamp: Optional[datetime] = None
    external_id: Optional[str] = None


def compute_hash(content: str) -> str:
    return hashlib.sha256(content.strip().encode("utf-8")).hexdigest()


# Handwritten date headers like "20 Jan 2026", "Wed 5th April", "23rd December",
# "Monday 15 December 2025". Anchored to the start of the first line so dates
# mentioned mid-sentence don't match; requires a month name so lines like
# "Goals 2024" or "1st draft" don't either.
_INLINE_DATE_RE = re.compile(
    r"^(?:(?:mon|tue(?:s)?|wed(?:nes)?|thu(?:rs)?|fri|sat(?:ur)?|sun)(?:day)?\s+)?"
    r"(\d{1,2})(?:st|nd|rd|th)?\s+"
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?"
    r"|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"(?:\s+(\d{4}|\d{2}))?\b",
    re.IGNORECASE,
)

_MONTH_NUM = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def parse_inline_date(content: str, fallback: Optional[datetime] = None) -> Optional[datetime]:
    """
    Parse a handwritten date header from the first line of an entry.

    Journal entries often open with the date they were written about
    (e.g. "20 Jan 2026\\n..."), which is more trustworthy than the note's
    created timestamp when entries are written up retrospectively.

    When the header has no year ("Wed 5th April"), the candidate year closest
    to `fallback` (or now) is chosen, so a December note synced in January
    still lands in December. Returns a naive-UTC datetime at midnight, or
    None when no header is found.
    """
    first_line = content.strip().split("\n", 1)[0].strip()
    m = _INLINE_DATE_RE.match(first_line)
    if m is None:
        return None

    day = int(m.group(1))
    month = _MONTH_NUM[m.group(2)[:3].lower()]
    ref = fallback or datetime.utcnow()

    if m.group(3):
        year = int(m.group(3))
        if year < 100:
            year += 2000
        candidates = [year]
    else:
        candidates = [ref.year - 1, ref.year, ref.year + 1]

    best: Optional[datetime] = None
    for year in candidates:
        try:
            candidate = datetime(year, month, day)
        except ValueError:  # e.g. 31 Feb
            continue
        if best is None or abs(candidate - ref) < abs(best - ref):
            best = candidate

    # Reject implausible years (typos like "20 Jan 226")
    if best is None or not 2000 <= best.year <= ref.year + 1:
        return None
    return best


def ingest_entries(
    raw_entries: List[Union[str, RawEntry]],
    source: str,
    session: Session,
) -> Tuple[dict, List[JournalEntry]]:
    """
    Persist new entries, skipping duplicates by content hash.

    Accepts plain strings (timestamped at ingest time) or RawEntry tuples that
    carry the source's own authored date. Returns (stats_dict, new_entries).
    """
    new_entries: List[JournalEntry] = []
    skipped = 0

    for raw in raw_entries:
        if isinstance(raw, RawEntry):
            content = raw.content.strip()
            authored_at = raw.timestamp
            external_id = raw.external_id
        else:
            content = raw.strip()
            authored_at = None
            external_id = None

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

        # A handwritten date header inside the entry outranks the source
        # timestamp (entries are sometimes written up retrospectively).
        # Same-day matches keep the source timestamp's time of day.
        inline = parse_inline_date(content, fallback=authored_at)
        if inline is not None and (authored_at is None or inline.date() != authored_at.date()):
            authored_at = inline

        entry = JournalEntry(
            source=source,
            content=content,
            timestamp=authored_at or datetime.utcnow(),
            content_hash=content_hash,
            external_id=external_id,
        )
        session.add(entry)
        session.commit()
        session.refresh(entry)

        embed_and_store(entry.id, content)
        new_entries.append(entry)

    return {"ingested": len(new_entries), "skipped": skipped}, new_entries


def redate_inline(session: Session) -> dict:
    """
    Backfill: re-date stored entries from their handwritten date headers.

    Parses each entry's first line; where it names a different calendar day
    than the stored timestamp, the timestamp is overwritten (content,
    embeddings, and mood_score untouched).
    """
    entries = session.exec(select(JournalEntry)).all()

    updated = unchanged = no_header = 0
    for entry in entries:
        parsed = parse_inline_date(entry.content, fallback=entry.timestamp)
        if parsed is None:
            no_header += 1
            continue
        if parsed.date() == entry.timestamp.date():
            unchanged += 1
            continue
        entry.timestamp = parsed
        session.add(entry)
        updated += 1

    session.commit()
    stats = {
        "total": len(entries),
        "updated": updated,
        "unchanged": unchanged,
        "no_header": no_header,
    }
    logger.info("Inline re-date: %s", stats)
    return stats


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


def _fetch_keep_entries() -> List[RawEntry]:
    """Blocking: authenticate to Google Keep and return every note as a
    RawEntry carrying its real creation date. Run inside an executor."""
    import gkeepapi

    keep = gkeepapi.Keep()
    keep.authenticate(os.environ["GOOGLE_EMAIL"], os.environ["GOOGLE_APP_MASTER_TOKEN"])
    keep.sync()

    entries: List[RawEntry] = []
    for note in keep.all():
        parts: List[str] = []
        if getattr(note, "title", None):
            parts.append(note.title)
        if getattr(note, "text", None):
            parts.append(note.text)
        combined = "\n".join(parts)
        if not combined.strip():
            continue

        # Keep notes carry their own authored date — use the note's
        # creation time so entries land on the day they were written,
        # not the day we happened to sync. gkeepapi yields tz-aware UTC;
        # drop the tzinfo to match the DB's naive-UTC convention.
        authored_at: Optional[datetime] = None
        ts = getattr(note, "timestamps", None)
        created = getattr(ts, "created", None)
        if isinstance(created, datetime):
            authored_at = created.replace(tzinfo=None)

        entries.append(
            RawEntry(content=combined, timestamp=authored_at, external_id=note.id)
        )
    return entries


async def ingest_keep(session: Session) -> Tuple[dict, List[JournalEntry]]:
    """Fetch all Google Keep notes and persist new ones (idempotent via content hash)."""
    entries = await asyncio.get_running_loop().run_in_executor(None, _fetch_keep_entries)
    logger.info("Google Keep: fetched %d notes", len(entries))
    return ingest_entries(entries, "google_keep", session)


async def redate_keep(session: Session) -> dict:
    """
    Backfill correct authored dates onto already-ingested Keep entries.

    Re-fetches Keep, matches each note to a stored entry by content hash, and
    overwrites only `timestamp` (leaving content, embeddings, and mood_score
    untouched). For repairing entries ingested before date-capture existed.
    """
    entries = await asyncio.get_running_loop().run_in_executor(None, _fetch_keep_entries)

    updated = unchanged = unmatched = no_date = 0
    for raw in entries:
        if raw.timestamp is None:
            no_date += 1
            continue

        existing = session.exec(
            select(JournalEntry).where(
                JournalEntry.content_hash == compute_hash(raw.content)
            )
        ).first()
        if existing is None:
            unmatched += 1
            continue

        if existing.timestamp == raw.timestamp:
            unchanged += 1
            continue

        existing.timestamp = raw.timestamp
        session.add(existing)
        updated += 1

    session.commit()
    stats = {
        "fetched": len(entries),
        "updated": updated,
        "unchanged": unchanged,
        "unmatched": unmatched,
        "no_date": no_date,
    }
    logger.info("Google Keep re-date: %s", stats)
    return stats


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
