"""Tests for the ingestion service's async/external paths and date helpers
(gkeepapi, the LLM choke point, and the filesystem are mocked)."""
from datetime import datetime
from types import SimpleNamespace

import pypdf
import pytest
from sqlmodel import select

import services.ingestion as ing
from models.journal import JournalEntry
from models.user import User
from services.ingestion import RawEntry
from services.quota import QuotaExceededError


@pytest.fixture
def sync_owner(session, monkeypatch):
    """Give the Keep/GoodNotes connectors a resolvable owner.

    Both resolve their rows' owner via `_resolve_sync_owner` (GOOGLE_EMAIL → the
    matching user). Since the Postgres migration JournalEntry.user_id is NOT
    NULL, so a sync with no resolvable owner ingests nothing — set up the env +
    a matching user so persistence tests actually persist. Returns the owner.
    """
    owner = User(google_sub="sync-owner", email="sync@owner.com")
    session.add(owner)
    session.commit()
    session.refresh(owner)
    monkeypatch.setenv("GOOGLE_EMAIL", "sync@owner.com")
    return owner


# --- _rescue_regressed_date -------------------------------------------------

def test_rescue_regressed_date_reanchors_onto_running_month():
    # Day numeral is usually right even when OCR misreads the month; re-anchor it.
    assert ing._rescue_regressed_date("18 Jan 2026\nbody", datetime(2025, 5, 10)) == datetime(2025, 5, 18)


def test_rescue_regressed_date_rolls_to_next_month():
    # Day 3 is before the running day-25, so it belongs to the next month.
    assert ing._rescue_regressed_date("3 Jan 2026\nbody", datetime(2025, 5, 25)) == datetime(2025, 6, 3)


def test_rescue_regressed_date_no_header_returns_none():
    assert ing._rescue_regressed_date("no date here", datetime(2025, 5, 10)) is None


# --- _page_already_imported -------------------------------------------------

def test_page_already_imported(session):
    session.add(JournalEntry(
        user_id=1,
        source="goodnotes", content="p", content_hash="h1",
        external_id="Journal.pdf#p1", imported_at=datetime(2026, 1, 11),
    ))
    session.commit()
    # Existence-based, mtime-independent: any matching goodnotes page → True.
    assert ing._page_already_imported(session, "Journal.pdf#p1") is True
    assert ing._page_already_imported(session, "Journal.pdf#p2") is False
    assert ing._page_already_imported(session, "missing") is False


def test_page_already_imported_ignores_other_sources(session):
    # Same external_id under a different source must not count as imported.
    session.add(JournalEntry(
        user_id=1,
        source="google_keep", content="p", content_hash="h2",
        external_id="Journal.pdf#p1",
    ))
    session.commit()
    assert ing._page_already_imported(session, "Journal.pdf#p1") is False


# --- redate_inline ----------------------------------------------------------

def test_redate_inline_updates_from_header(session):
    e = JournalEntry(
        user_id=1,
        source="text", content="20 Jan 2026\nbody", content_hash="h1", timestamp=datetime(2026, 1, 1)
    )
    session.add(e)
    session.commit()
    stats = ing.redate_inline(session)
    session.refresh(e)
    assert e.timestamp.date() == datetime(2026, 1, 20).date()
    assert stats["updated"] == 1


# --- _fetch_keep_entries (gkeepapi mocked) ---------------------------------

def test_fetch_keep_entries(monkeypatch):
    import gkeepapi

    note = SimpleNamespace(
        title="Title", text="body text", id="note-1",
        timestamps=SimpleNamespace(created=datetime(2026, 1, 5)),
    )
    fake_keep = SimpleNamespace(
        authenticate=lambda email, token: None,
        sync=lambda: None,
        all=lambda: [note],
    )
    monkeypatch.setattr(gkeepapi, "Keep", lambda: fake_keep)
    monkeypatch.setenv("GOOGLE_EMAIL", "x@y.com")
    monkeypatch.setenv("GOOGLE_APP_MASTER_TOKEN", "tok")

    entries = ing._fetch_keep_entries()
    assert len(entries) == 1
    assert entries[0].content == "Title\nbody text"
    assert entries[0].external_id == "note-1"
    assert entries[0].timestamp == datetime(2026, 1, 5)


# --- ingest_keep (uses _fetch_keep_entries via executor) -------------------

async def test_ingest_keep_persists_notes(session, monkeypatch, sync_owner):
    monkeypatch.setattr(
        ing, "_fetch_keep_entries",
        lambda: [RawEntry(content="keep note", external_id="k1", timestamp=datetime(2026, 1, 5))],
    )
    stats, scored = await ing.ingest_keep(session)
    assert stats["ingested"] == 1
    rows = session.exec(select(JournalEntry)).all()
    assert rows[0].source == "google_keep"
    assert len(scored) == 1


# --- _ocr_with_claude (via the llm_client choke point, stream mocked) ------

async def test_ocr_routes_through_the_llm_choke_point(monkeypatch):
    """OCR must go through stream_text — the choke point where quota, metering,
    and BYO-key resolution live — with a task-fixed model and the owner billed."""
    seen = {}

    async def fake_stream_text(*, messages, model, max_tokens, user_id):
        seen.update(model=model, user_id=user_id, messages=messages)
        yield "transcribed "
        yield "text"

    monkeypatch.setattr(ing, "stream_text", fake_stream_text)

    out = await ing._ocr_with_claude(b"bytes", "application/pdf", user_id=7)
    assert out == "transcribed text"
    assert seen["model"] == ing._OCR_MODEL  # task-fixed (vision), never resolved
    assert seen["user_id"] == 7  # billed to the entry owner
    # The page rides as a document block ahead of the transcription prompt.
    assert seen["messages"][0]["content"][0]["type"] == "document"


async def test_ocr_sends_images_as_image_blocks(monkeypatch):
    seen = {}

    async def fake_stream_text(*, messages, model, max_tokens, user_id):
        seen["block_type"] = messages[0]["content"][0]["type"]
        yield "a note"

    monkeypatch.setattr(ing, "stream_text", fake_stream_text)
    await ing._ocr_with_claude(b"\x89PNG", "image/png", user_id=None)
    assert seen["block_type"] == "image"


# --- _ensure_local (non-iCloud passthrough) --------------------------------

async def test_ensure_local_passthrough(tmp_path):
    f = tmp_path / "real.pdf"
    f.write_bytes(b"x")
    assert await ing._ensure_local(f) == f


# --- ingest_goodnotes (temp PDF + mocked OCR + mocked client) --------------

def _write_pdf(path):
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with open(path, "wb") as fh:
        writer.write(fh)


async def test_ingest_goodnotes_ingests_page(session, monkeypatch, tmp_path, sync_owner):
    _write_pdf(tmp_path / "Journal.pdf")
    monkeypatch.setenv("GOODNOTES_PATH", str(tmp_path))

    async def fake_ocr(file_bytes, media_type, *, user_id):
        return "10 Jan 2026\nGreat productive day"

    monkeypatch.setattr(ing, "_ocr_with_claude", fake_ocr)

    stats, scored = await ing.ingest_goodnotes(session)
    assert stats["ingested"] == 1
    row = session.exec(select(JournalEntry)).all()[0]
    assert row.source == "goodnotes"
    assert row.external_id == "Journal.pdf#p1"
    assert row.timestamp.date() == datetime(2026, 1, 10).date()


async def test_ingest_goodnotes_skips_blank_pages(session, monkeypatch, tmp_path):
    _write_pdf(tmp_path / "Journal.pdf")
    monkeypatch.setenv("GOODNOTES_PATH", str(tmp_path))

    async def blank_ocr(file_bytes, media_type, *, user_id):
        return "[BLANK]"

    monkeypatch.setattr(ing, "_ocr_with_claude", blank_ocr)
    stats, _ = await ing.ingest_goodnotes(session)
    assert stats["ingested"] == 0


async def test_ingest_goodnotes_image_file(session, monkeypatch, tmp_path, sync_owner):
    (tmp_path / "photo.png").write_bytes(b"\x89PNG fake-bytes")
    monkeypatch.setenv("GOODNOTES_PATH", str(tmp_path))

    async def fake_ocr(file_bytes, media_type, *, user_id):
        return "A handwritten note"

    monkeypatch.setattr(ing, "_ocr_with_claude", fake_ocr)
    stats, _ = await ing.ingest_goodnotes(session)
    assert stats["ingested"] == 1
    row = session.exec(select(JournalEntry)).all()[0]
    assert row.external_id == "photo.png"


async def test_ingest_goodnotes_quota_stops_scan_but_keeps_paid_pages(
    session, monkeypatch, tmp_path, sync_owner
):
    """A quota refusal mid-scan stops further OCR (every later call would be
    refused too) but still ingests the pages already transcribed — that money
    is spent, so the transcripts must not be thrown away."""
    _write_pdf_pages(tmp_path / "Journal.pdf", 3)
    monkeypatch.setenv("GOODNOTES_PATH", str(tmp_path))
    calls = {"n": 0}

    async def quota_after_one(file_bytes, media_type, *, user_id):
        calls["n"] += 1
        if calls["n"] > 1:
            raise QuotaExceededError("budget exhausted")
        return "10 Jan 2026\nFirst page"

    monkeypatch.setattr(ing, "_ocr_with_claude", quota_after_one)
    stats, _ = await ing.ingest_goodnotes(session)
    assert stats["ingested"] == 1  # the transcribed page survived
    assert calls["n"] == 2  # page 2 was refused; page 3 never attempted


async def test_ingest_goodnotes_missing_folder(session, monkeypatch):
    monkeypatch.setenv("GOODNOTES_PATH", "/no/such/path/xyz")
    stats, entries = await ing.ingest_goodnotes(session)
    assert stats["ingested"] == 0
    assert stats["updated"] == 0
    assert stats["skipped"] == 0
    assert entries == []
    # A stale path must not read as "nothing new" — that ambiguity hid a
    # 7-week ingestion outage when Drive renamed the export folder.
    assert "/no/such/path/xyz" in stats["error"]


async def test_ingest_goodnotes_recurses_into_subfolders(
    session, monkeypatch, tmp_path, sync_owner
):
    """GOODNOTES_PATH points at the parent folder, so the scan must find the PDF
    inside Drive's renamed notebook subfolder ("Notes (1)")."""
    nested = tmp_path / "Notes (1)"
    nested.mkdir()
    _write_pdf(nested / "Journal_2026.pdf")
    monkeypatch.setenv("GOODNOTES_PATH", str(tmp_path))

    async def fake_ocr(file_bytes, media_type, *, user_id):
        return "14 Mar 2026\nfound it"

    monkeypatch.setattr(ing, "_ocr_with_claude", fake_ocr)

    stats, entries = await ing.ingest_goodnotes(session)
    assert stats["ingested"] == 1
    assert entries[0].external_id == "Journal_2026.pdf#p1"


# --- append-aware OCR skip (the regression guard) --------------------------

def _write_pdf_pages(path, n):
    """Write a PDF with `n` blank pages so each becomes its own page entry."""
    writer = pypdf.PdfWriter()
    for _ in range(n):
        writer.add_blank_page(width=200, height=200)
    with open(path, "wb") as fh:
        writer.write(fh)


class _OCRSpy:
    """Counting spy: returns a distinct dated transcript per page so pages
    don't collide on content hash."""

    def __init__(self):
        self.calls = 0

    async def __call__(self, file_bytes, media_type, *, user_id):
        self.calls += 1
        day = 9 + self.calls  # 10 Jan, 11 Jan, ...
        return f"{day} Jan 2026\nEntry number {self.calls}"


def _setup_goodnotes(monkeypatch, tmp_path, spy):
    monkeypatch.setenv("GOODNOTES_PATH", str(tmp_path))
    monkeypatch.setattr(ing, "_ocr_with_claude", spy)


async def test_ingest_goodnotes_skips_already_imported_pages(session, monkeypatch, tmp_path, sync_owner):
    # Re-exporting the SAME multi-page PDF must not re-OCR existing pages.
    _write_pdf_pages(tmp_path / "Journal.pdf", 3)
    spy = _OCRSpy()
    _setup_goodnotes(monkeypatch, tmp_path, spy)

    stats, _ = await ing.ingest_goodnotes(session)
    assert stats["ingested"] == 3
    assert spy.calls == 3

    # Second run on the unchanged file: zero new OCR calls.
    stats2, scored2 = await ing.ingest_goodnotes(session)
    assert spy.calls == 3  # unchanged — no re-OCR
    assert stats2["ingested"] == 0
    assert scored2 == []


async def test_ingest_goodnotes_ocrs_only_new_page_on_append(session, monkeypatch, tmp_path, sync_owner):
    # A re-export with one extra appended page OCRs only that new page.
    pdf = tmp_path / "Journal.pdf"
    _write_pdf_pages(pdf, 2)
    spy = _OCRSpy()
    _setup_goodnotes(monkeypatch, tmp_path, spy)

    await ing.ingest_goodnotes(session)
    assert spy.calls == 2

    _write_pdf_pages(pdf, 3)  # grew by one page
    stats, _ = await ing.ingest_goodnotes(session)
    assert spy.calls == 3  # only the appended page #p3
    assert stats["ingested"] == 1


async def test_ingest_goodnotes_force_reocrs_all_pages(session, monkeypatch, tmp_path):
    _write_pdf_pages(tmp_path / "Journal.pdf", 3)
    spy = _OCRSpy()
    _setup_goodnotes(monkeypatch, tmp_path, spy)

    await ing.ingest_goodnotes(session)
    assert spy.calls == 3

    # force=True re-OCRs every page even though all already exist.
    await ing.ingest_goodnotes(session, force=True)
    assert spy.calls == 6


async def test_redate_keep_updates_timestamp(session, monkeypatch):
    e = JournalEntry(
        user_id=1,
        source="google_keep", content="keep note",
        content_hash=ing.compute_hash("keep note"), timestamp=datetime(2026, 1, 1),
    )
    session.add(e)
    session.commit()
    monkeypatch.setattr(
        ing, "_fetch_keep_entries",
        lambda: [RawEntry(content="keep note", timestamp=datetime(2026, 1, 5))],
    )
    stats = await ing.redate_keep(session)
    session.refresh(e)
    assert e.timestamp == datetime(2026, 1, 5)
    assert stats["updated"] == 1
