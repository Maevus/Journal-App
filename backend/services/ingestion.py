"""
Entry ingestion: parse, deduplicate (content hash), persist, and
asynchronously score mood via the LLM.

Sources:
  - text        plain entries posted directly to /ingest
  - google_keep fetched via gkeepapi (GOOGLE_EMAIL + GOOGLE_APP_MASTER_TOKEN)
  - goodnotes   scanned from a local folder (GOODNOTES_PATH), e.g. the
                Google Drive Desktop mirror of the GoodNotes PDF backup
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

from pydantic import BaseModel
from sqlmodel import Session, select

from clock import utcnow_naive
from models.journal import JournalEntry, engine
from services.embeddings import embed_and_store
from services.llm_client import stream_text
from services.quota import QuotaExceededError

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
    ref = fallback or utcnow_naive()

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
    if best > utcnow_naive() + timedelta(days=1):
        return None
    return best


def ingest_entries(
    raw_entries: List[Union[str, RawEntry]],
    source: str,
    session: Session,
    *,
    user_id: Optional[int] = None,
) -> Tuple[dict, List[JournalEntry]]:
    """
    Persist entries: insert new ones, update edited ones, skip unchanged ones.

    Dedup is two-tier: an exact content-hash match means the entry is unchanged
    (skip); otherwise a row with the same source + external_id means the note
    was edited at its source — it is updated in place (content, hash, date,
    re-embedded, mood reset) rather than duplicated. Both tiers are scoped to
    `user_id` (ADR 0003): identical text from two accounts must both persist,
    and one user's source-edit must never overwrite another's row. `user_id` is
    required (JournalEntry.user_id is NOT NULL since the Postgres migration);
    None means the connector's owner isn't resolvable yet — see the guard below.

    Accepts plain strings (timestamped at ingest time) or RawEntry tuples that
    carry the source's own authored date. Returns (stats_dict, entries needing
    mood scoring — both newly inserted and updated).
    """
    if user_id is None:
        # Since the Postgres migration JournalEntry.user_id is NOT NULL, so an
        # unowned row can't be inserted. A connector sync whose owner isn't
        # resolvable yet (its account hasn't logged in) ingests nothing and
        # defers — the pages re-present on the next sync once the owner exists —
        # rather than crashing on the NOT NULL insert.
        logger.info(
            "Ingest skipped for source %r: no owner resolved (%d item(s) deferred)",
            source, len(raw_entries),
        )
        return {"ingested": 0, "updated": 0, "skipped": len(raw_entries)}, []

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
            select(JournalEntry).where(
                JournalEntry.content_hash == content_hash,
                JournalEntry.user_id == user_id,
            )
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
                    JournalEntry.user_id == user_id,
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
                prior.mood_reasoning = None
                session.add(prior)
                session.commit()
                session.refresh(prior)

                embed_and_store(prior.id, content, user_id=user_id)
                updated_entries.append(prior)
                continue

        entry = JournalEntry(
            source=source,
            content=content,
            timestamp=authored_at or utcnow_naive(),
            content_hash=content_hash,
            external_id=external_id,
            user_id=user_id,
        )
        session.add(entry)
        session.commit()
        session.refresh(entry)

        embed_and_store(entry.id, content, user_id=user_id)
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


# Chars of entry text sent to the mood scorer. Longer entries are truncated to
# this; the score then reflects only the opening. ~32% of entries exceed 600, so
# this was raised from 600 to 2000 to cover the bulk of them (p90 ≈ 1515 chars).
_MOOD_CONTENT_CHARS = 2000


class _MoodScore(BaseModel):
    score: float  # 0.0 = very negative, 1.0 = very positive
    reasoning: str


async def score_mood_background(entry_id: int, content: str, *, user_id: Optional[int]) -> None:
    """
    Background task: call the LLM for a mood score and write it back to the DB.
    Failures are silently swallowed — scoring is best-effort.

    `user_id` is the entry's owner — scoring their entry spends money on their
    behalf. None means an unowned row (a connector sync before its account has
    logged in): there is no owner to bill, so scoring is skipped (F3) rather
    than spending the app key. Those rows get scored on the owner's budget once
    they're claimed (scripts/backfill_mood_reasoning.py).
    """
    if user_id is None:
        logger.info("Mood scoring skipped for entry %d (unowned — no owner to bill)", entry_id)
        return
    try:
        from services.llm_client import structured_output

        result = await structured_output(
            prompt=f"Analyse the mood of this journal entry:\n\n{content[:_MOOD_CONTENT_CHARS]}",
            output_schema=_MoodScore,
            system=(
                "You are an emotional-intelligence expert. "
                "Score the mood of a journal entry on a scale from 0.0 (very negative) "
                "to 1.0 (very positive). Return a precise float and a one-sentence reasoning."
            ),
            # Room for score + a sentence of reasoning. 128 truncated mid-JSON;
            # 256 still truncated long reasonings (invalid tool JSON → validation
            # error), so 512 for headroom.
            max_tokens=512,
            user_id=user_id,
        )

        with Session(engine) as session:
            entry = session.get(JournalEntry, entry_id)
            if entry:
                entry.mood_score = max(0.0, min(1.0, result.score))
                entry.mood_reasoning = result.reasoning
                session.commit()

    except QuotaExceededError as exc:
        # Expected under F2, not a failure: the budget gate refused before any
        # spend. INFO so a day of quota-skipped scorings reads as what it is.
        logger.info("Mood scoring skipped for entry %d (quota): %s", entry_id, exc)
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


async def _ocr_with_claude(file_bytes: bytes, media_type: str, *, user_id: Optional[int]) -> str:
    """Transcribe handwritten or printed content via Claude vision.

    Goes through llm_client.stream_text — the same choke point as every other
    LLM call — so OCR is quota-gated before it spends, metered after, and runs
    on the owner's BYO key when they have one. (It previously used a raw
    Anthropic client and was the one unmetered/ungated spend surface.) The
    model stays task-fixed (`_OCR_MODEL` needs vision), which per F3 means it's
    honored as-is and never per-user resolved. `user_id` is the entry owner
    from _resolve_sync_owner: their pages, their budget; None (unowned sync) is
    system work — metered, gated only by the global cap.
    """
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

    parts: List[str] = []
    async for chunk in stream_text(
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
        model=_OCR_MODEL,
        max_tokens=2048,
        user_id=user_id,
    ):
        parts.append(chunk)
    return "".join(parts)


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


def _resolve_sync_owner(session: Session) -> Optional[int]:
    """Who owns entries from the Keep/GoodNotes connectors.

    Both connectors are env-configured for exactly one Google account
    (GOOGLE_EMAIL) — pre-multi-user legacy; per-user connector config comes
    later. Their entries belong to that account's user. Before that person
    has ever logged in, rows stay unowned (NULL) — made visible by running
    scripts/backfill_owner.py once, or permanently at the Postgres migration.
    """
    from models.user import User

    email = os.getenv("GOOGLE_EMAIL")
    if not email:
        return None
    user = session.exec(select(User).where(User.email == email)).first()
    return user.id if user is not None else None


async def ingest_keep(session: Session) -> Tuple[dict, List[JournalEntry]]:
    """Fetch all Google Keep notes and persist new ones (idempotent via content hash)."""
    entries = await asyncio.get_running_loop().run_in_executor(None, _fetch_keep_entries)
    logger.info("Google Keep: fetched %d notes", len(entries))
    return ingest_entries(
        entries, "google_keep", session, user_id=_resolve_sync_owner(session)
    )


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
# GoodNotes (local folder) ingester
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
        if last_date <= candidate <= utcnow_naive() + timedelta(days=1):
            return candidate
    return None


def _is_blank_transcript(text: str) -> bool:
    """True when the OCR output denotes an empty page rather than content —
    the model sometimes narrates blankness instead of staying silent."""
    t = text.strip().lower()
    if not t or t == "[blank]":
        return True
    return len(t) < 80 and ("no text" in t or "blank" in t or "nothing to transcribe" in t)


def _page_already_imported(session: Session, external_id: str) -> bool:
    """True if a GoodNotes entry already exists for this page's external_id.

    The GoodNotes backup is one ever-growing PDF (≈1 page/day) re-exported in
    full, so every re-export re-presents all prior pages. Skipping OCR by page
    existence — not file mtime — means only genuinely new (appended) pages get a
    paid Claude-vision call; re-exporting the same file costs nothing."""
    existing = session.exec(
        select(JournalEntry).where(
            JournalEntry.source == "goodnotes",
            JournalEntry.external_id == external_id,
        )
    ).first()
    return existing is not None


async def ingest_goodnotes(
    session: Session, force: bool = False
) -> Tuple[dict, List[JournalEntry]]:
    """
    Scan the local GoodNotes folder and ingest each PDF page (or image) as
    its own journal entry, transcribed via Claude vision.

    Page identity is "<filename>#p<N>" in external_id. The backup is one
    ever-growing PDF re-exported in full, so already-ingested pages are skipped
    by existence (`_page_already_imported`) before any paid OCR call — only
    appended pages are transcribed. Entry dates come from handwritten date
    headers (parse_inline_date), falling back to file mtime.

    `force=True` re-OCRs every page even when it already exists (edited pages
    then update in place; unchanged pages dedupe by content hash).
    """
    raw_path = os.environ["GOODNOTES_PATH"]
    folder = Path(os.path.expanduser(raw_path))

    if not folder.exists():
        # A configured-but-absent path is an operator error, not an empty inbox,
        # and the two used to be indistinguishable: this returned plain zeros, so
        # a scan over a stale path logged exactly what "no new pages" logs. Google
        # Drive renaming the export folder ("Notes" → "Notes (1)") stranded the
        # connector for 7 weeks that way. ERROR + an explicit `error` key so the
        # scheduled scan and a manual /ingest both say which it is.
        logger.error(
            "GoodNotes path does not exist: %s — connector is ingesting NOTHING "
            "until GOODNOTES_PATH is corrected", folder,
        )
        return {
            "ingested": 0, "updated": 0, "skipped": 0,
            "error": f"GOODNOTES_PATH does not exist: {folder}",
        }, []

    # OCR spends money, so it's billed to the sync owner (their pages, their
    # budget/key) and quota-gated per call. Resolved once, up front.
    owner = _resolve_sync_owner(session)
    raw_entries: List[RawEntry] = []
    ocr_calls = 0
    quota_hit = False

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
                if not force and _page_already_imported(session, external_id):
                    continue
                try:
                    text = await _ocr_with_claude(page_bytes, "application/pdf", user_id=owner)
                except QuotaExceededError as exc:
                    # The gate refused BEFORE spending; every further page would
                    # be refused too. Stop scanning but keep (and ingest) the
                    # pages already transcribed — that money is spent.
                    logger.info("GoodNotes OCR stopped by quota: %s", exc)
                    quota_hit = True
                    break
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
            if not force and _page_already_imported(session, external_id):
                continue
            try:
                text = await _ocr_with_claude(
                    resolved.read_bytes(), _IMAGE_MEDIA_TYPES[suffix], user_id=owner
                )
            except QuotaExceededError as exc:
                logger.info("GoodNotes OCR stopped by quota: %s", exc)
                quota_hit = True
                break
            ocr_calls += 1
            if not _is_blank_transcript(text):
                raw_entries.append(
                    RawEntry(content=text, timestamp=file_mtime, external_id=external_id)
                )
        if quota_hit:
            break

    logger.info(
        "GoodNotes: %d OCR call(s), %d page(s) with text in %s",
        ocr_calls, len(raw_entries), folder,
    )
    return ingest_entries(raw_entries, "goodnotes", session, user_id=owner)
