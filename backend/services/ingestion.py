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
import io
import logging
import os
import re
from datetime import datetime, timedelta, timezone
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
# "Monday 15 December 2025", "17th of May", "DATE: 15th May", "23d May".
# Anchored to the start of the first line so dates mentioned mid-sentence
# don't match; requires a month name so lines like "Goals 2024" or
# "1st draft" don't either.
_INLINE_DATE_RE = re.compile(
    r"^(?:date[:\s]+)?"
    r"(?:(?:mon|tue(?:s)?|wed(?:nes)?|thu(?:rs)?|fri|sat(?:ur)?|sun)(?:day)?\s+)?"
    r"(\d{1,2})(?:st|nd|rd|th|d)?\s+(?:of\s+)?"
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
    # Reject future dates — journal entries describe the past; a "future"
    # header is an OCR misread (e.g. handwritten May read as Nov).
    if best > datetime.utcnow() + timedelta(days=1):
        return None
    return best


def ingest_entries(
    raw_entries: List[Union[str, RawEntry]],
    source: str,
    session: Session,
) -> Tuple[dict, List[JournalEntry]]:
    """
    Persist entries: insert new ones, update edited ones, skip unchanged ones.

    Dedup is two-tier: an exact content-hash match means the entry is unchanged
    (skip); otherwise a row with the same source + external_id means the note
    was edited at its source — it is updated in place (content, hash, date,
    re-embedded, mood reset) rather than duplicated.

    Accepts plain strings (timestamped at ingest time) or RawEntry tuples that
    carry the source's own authored date. Returns (stats_dict, entries needing
    mood scoring — both newly inserted and updated).
    """
    new_entries: List[JournalEntry] = []
    updated_entries: List[JournalEntry] = []
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

        if external_id is not None:
            prior = session.exec(
                select(JournalEntry).where(
                    JournalEntry.source == source,
                    JournalEntry.external_id == external_id,
                )
            ).first()
            if prior is not None:
                # Same note, new content — edited at the source. Update in
                # place so edits don't accumulate as stale duplicates.
                prior.content = content
                prior.content_hash = content_hash
                if authored_at is not None:
                    prior.timestamp = authored_at
                prior.mood_score = None  # stale for the new text; re-scored below
                session.add(prior)
                session.commit()
                session.refresh(prior)

                embed_and_store(prior.id, content)
                updated_entries.append(prior)
                continue

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

    stats = {
        "ingested": len(new_entries),
        "updated": len(updated_entries),
        "skipped": skipped,
    }
    return stats, new_entries + updated_entries


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
            # Room for score + a sentence of reasoning; 128 truncated mid-JSON
            max_tokens=256,
        )

        with Session(engine) as session:
            entry = session.get(JournalEntry, entry_id)
            if entry:
                entry.mood_score = max(0.0, min(1.0, result.score))
                session.commit()

    except Exception as exc:  # noqa: BLE001
        logger.warning("Mood scoring failed for entry %d: %s", entry_id, exc)


# ---------------------------------------------------------------------------
# OCR helper — Claude vision (handwriting transcription)
# GoodNotes' embedded OCR text layer is too garbled to use; Claude reads the
# page images directly instead.
# ---------------------------------------------------------------------------

# claude-3-5-sonnet was retired 2025-10-28; sonnet-4-6 is the documented replacement
_OCR_MODEL = "claude-sonnet-4-6"
_IMAGE_MEDIA_TYPES: Dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


async def _ocr_with_claude(
    client: "anthropic.AsyncAnthropic", file_bytes: bytes, media_type: str
) -> str:
    """Transcribe handwritten or printed content via Claude vision."""
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
        max_tokens=2048,
        messages=[
            {
                "role": "user",
                "content": [
                    content_block,
                    {
                        "type": "text",
                        "text": (
                            "Transcribe all handwritten or printed text from this journal page. "
                            "If the page starts with a date header, keep it as the first line. "
                            "Output only the transcribed text, no commentary. "
                            "If the page is blank or has no text, output exactly [BLANK] and nothing else."
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


def _split_pdf_pages(path: Path) -> List[bytes]:
    """Split a PDF into single-page PDFs (bytes), so each page can be OCR'd
    and ingested as its own journal entry."""
    import pypdf

    pages: List[bytes] = []
    with open(path, "rb") as fh:
        reader = pypdf.PdfReader(fh)
        for page in reader.pages:
            writer = pypdf.PdfWriter()
            writer.add_page(page)
            buf = io.BytesIO()
            writer.write(buf)
            pages.append(buf.getvalue())
    return pages


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


def _rescue_regressed_date(text: str, last_date: datetime) -> Optional[datetime]:
    """
    When a page's parsed date goes *backward* in a chronological notebook,
    the month word was likely misread by OCR ("May" → "Nov") while the day
    numeral is usually right. Re-anchor the day onto the running month (or
    the next), keeping the result within [last_date, now].
    """
    first_line = text.strip().split("\n", 1)[0].strip()
    m = _INLINE_DATE_RE.match(first_line)
    if m is None:
        return None
    day = int(m.group(1))

    candidates = [
        (last_date.year, last_date.month),
        (last_date.year + (last_date.month == 12), last_date.month % 12 + 1),
    ]
    for year, month in candidates:
        try:
            candidate = datetime(year, month, day)
        except ValueError:
            continue
        if last_date <= candidate <= datetime.utcnow() + timedelta(days=1):
            return candidate
    return None


def _is_blank_transcript(text: str) -> bool:
    """True when the OCR output denotes an empty page rather than content —
    the model sometimes narrates blankness instead of staying silent."""
    t = text.strip().lower()
    if not t or t == "[blank]":
        return True
    return len(t) < 80 and ("no text" in t or "blank" in t or "nothing to transcribe" in t)


def _already_imported(session: Session, external_id: str, file_mtime: datetime) -> bool:
    """True if this page was ingested after the file was last modified —
    meaning the export is unchanged and re-OCR would be wasted spend."""
    existing = session.exec(
        select(JournalEntry).where(
            JournalEntry.source == "goodnotes",
            JournalEntry.external_id == external_id,
        )
    ).first()
    return (
        existing is not None
        and existing.imported_at is not None
        and existing.imported_at >= file_mtime
    )


async def ingest_goodnotes(session: Session) -> Tuple[dict, List[JournalEntry]]:
    """
    Scan the iCloud GoodNotes folder and ingest each PDF page (or image) as
    its own journal entry, transcribed via Claude vision.

    Page identity is "<filename>#p<N>" in external_id: re-exporting the same
    file re-OCRs only when the file changed, edited pages update in place,
    and unchanged pages dedupe by content hash. Entry dates come from
    handwritten date headers (parse_inline_date), falling back to file mtime.
    """
    raw_path = os.environ["GOODNOTES_ICLOUD_PATH"]
    folder = Path(os.path.expanduser(raw_path))

    if not folder.exists():
        logger.warning("GoodNotes iCloud path does not exist: %s", folder)
        return {"ingested": 0, "updated": 0, "skipped": 0}, []

    client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    raw_entries: List[RawEntry] = []
    ocr_calls = 0

    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.name.startswith(".DS_Store"):
            continue

        resolved = await _ensure_local(path)
        if resolved is None:
            continue

        suffix = resolved.suffix.lower()
        if suffix != ".pdf" and suffix not in _IMAGE_MEDIA_TYPES:
            continue

        file_mtime = datetime.fromtimestamp(
            resolved.stat().st_mtime, tz=timezone.utc
        ).replace(tzinfo=None)

        if suffix == ".pdf":
            page_pdfs = _split_pdf_pages(resolved)
            # Notebook pages are chronological: pages without a parseable
            # date header inherit the date of the last dated page before them.
            last_date: Optional[datetime] = None
            for page_num, page_bytes in enumerate(page_pdfs, start=1):
                external_id = f"{resolved.name}#p{page_num}"
                if _already_imported(session, external_id, file_mtime):
                    continue
                text = await _ocr_with_claude(client, page_bytes, "application/pdf")
                ocr_calls += 1
                if _is_blank_transcript(text):
                    continue
                parsed = parse_inline_date(text, fallback=last_date or file_mtime)
                # Notebooks move forward in time: a parsed date earlier than
                # the running date is an OCR misread — rescue the day onto the
                # running month, or inherit the running date.
                if parsed is not None and last_date is not None and parsed < last_date:
                    parsed = _rescue_regressed_date(text, last_date)
                if parsed is not None:
                    last_date = parsed
                raw_entries.append(
                    RawEntry(
                        content=text,
                        timestamp=parsed or last_date or file_mtime,
                        external_id=external_id,
                    )
                )
        else:
            external_id = resolved.name
            if _already_imported(session, external_id, file_mtime):
                continue
            text = await _ocr_with_claude(
                client, resolved.read_bytes(), _IMAGE_MEDIA_TYPES[suffix]
            )
            ocr_calls += 1
            if not _is_blank_transcript(text):
                raw_entries.append(
                    RawEntry(content=text, timestamp=file_mtime, external_id=external_id)
                )

    logger.info(
        "GoodNotes: %d OCR call(s), %d page(s) with text in %s",
        ocr_calls, len(raw_entries), folder,
    )
    return ingest_entries(raw_entries, "goodnotes", session)
