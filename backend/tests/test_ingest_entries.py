"""Tests for the dedup/insert/update engine — the core of the ingest pipeline."""
from datetime import datetime

from sqlmodel import select

from models.journal import JournalEntry
from services.ingestion import RawEntry, compute_hash, ingest_entries


def _all(session):
    return session.exec(select(JournalEntry)).all()


def test_inserts_new_entry(session):
    stats, scored = ingest_entries(["a good day"], "text", session)
    assert stats == {"ingested": 1, "updated": 0, "skipped": 0}
    rows = _all(session)
    assert len(rows) == 1
    assert rows[0].content == "a good day"
    assert rows[0].source == "text"
    assert rows[0].external_id is None
    assert rows[0].mood_score is None
    assert rows[0].content_hash == compute_hash("a good day")
    assert len(scored) == 1  # returned for mood scoring


def test_skips_blank_entries(session):
    stats, scored = ingest_entries(["", "   ", "\n\t"], "text", session)
    assert stats["ingested"] == 0
    assert stats["skipped"] == 3
    assert _all(session) == []
    assert scored == []


def test_skips_exact_duplicate_within_batch(session):
    stats, _ = ingest_entries(["dupe", "dupe"], "text", session)
    assert stats["ingested"] == 1
    assert stats["skipped"] == 1
    assert len(_all(session)) == 1


def test_dedup_is_whitespace_insensitive(session):
    ingest_entries(["hello world"], "text", session)
    stats, scored = ingest_entries(["  hello world  "], "text", session)
    assert stats["ingested"] == 0
    assert stats["skipped"] == 1
    assert scored == []
    assert len(_all(session)) == 1


def test_external_id_edit_updates_in_place(session):
    ingest_entries([RawEntry(content="v1", external_id="k1")], "google_keep", session)
    first_id = _all(session)[0].id

    stats, scored = ingest_entries(
        [RawEntry(content="v2", external_id="k1")], "google_keep", session
    )
    assert stats == {"ingested": 0, "updated": 1, "skipped": 0}
    rows = _all(session)
    assert len(rows) == 1  # updated in place, not duplicated
    assert rows[0].id == first_id
    assert rows[0].content == "v2"
    assert rows[0].content_hash == compute_hash("v2")
    assert len(scored) == 1  # re-scored


def test_external_id_unchanged_content_is_skipped(session):
    ingest_entries([RawEntry(content="same", external_id="k1")], "google_keep", session)
    stats, scored = ingest_entries(
        [RawEntry(content="same", external_id="k1")], "google_keep", session
    )
    assert stats == {"ingested": 0, "updated": 0, "skipped": 1}
    assert scored == []
    assert len(_all(session)) == 1


def test_edit_resets_mood(session):
    ingest_entries([RawEntry(content="v1", external_id="k1")], "google_keep", session)
    row = _all(session)[0]
    row.mood_score = 0.7
    row.mood_reasoning = "happy"
    session.add(row)
    session.commit()

    ingest_entries([RawEntry(content="v2", external_id="k1")], "google_keep", session)
    row = _all(session)[0]
    assert row.mood_score is None
    assert row.mood_reasoning is None


def test_inline_date_header_sets_timestamp(session):
    ingest_entries(["20 Jan 2026\nGreat start to the year."], "text", session)
    row = _all(session)[0]
    assert row.timestamp.date() == datetime(2026, 1, 20).date()
