"""Per-user isolation — schema + per-user dedup (ADR 0003).

Identical text from two users hashes identically, so a *global*
content-hash dedup would silently drop the second user's entry. The dedup
key is per-user — composite (user_id, content_hash) — keeping the hash a
pure content fingerprint and user_id an orthogonal scoping dimension.
"""
from sqlmodel import select

from models.journal import JournalEntry
from services.ingestion import RawEntry, ingest_entries


def test_same_content_different_users_both_persist(session):
    stats_a, _ = ingest_entries(["Slept badly."], "text", session, user_id=1)
    stats_b, _ = ingest_entries(["Slept badly."], "text", session, user_id=2)

    assert stats_a["ingested"] == 1
    assert stats_b["ingested"] == 1  # not deduped across accounts
    rows = session.exec(select(JournalEntry)).all()
    assert {r.user_id for r in rows} == {1, 2}


def test_same_content_same_user_dedupes(session):
    ingest_entries(["Slept badly."], "text", session, user_id=1)
    stats, _ = ingest_entries(["Slept badly."], "text", session, user_id=1)

    assert stats["skipped"] == 1
    assert len(session.exec(select(JournalEntry)).all()) == 1


def test_writes_stamp_the_owner(session):
    _, entries = ingest_entries(["Went climbing."], "text", session, user_id=7)

    assert [e.user_id for e in entries] == [7]


def test_unowned_ingest_still_works(session):
    """Startup Keep sync / GoodNotes scan run outside a request — no user.
    Their rows stay unowned (NULL) until the recorded backfill migration."""
    _, entries = ingest_entries(["Legacy note."], "text", session)

    assert [e.user_id for e in entries] == [None]


def test_source_edit_update_is_user_scoped(session):
    """Same source + external_id from two users must never cross-update:
    the edited-at-source update path has to match within the owner only."""
    a = RawEntry(content="v1", timestamp=None, external_id="n1")
    b = RawEntry(content="v2", timestamp=None, external_id="n1")
    ingest_entries([a], "google_keep", session, user_id=1)
    ingest_entries([b], "google_keep", session, user_id=2)

    rows = session.exec(select(JournalEntry)).all()
    assert sorted((r.user_id, r.content) for r in rows) == [(1, "v1"), (2, "v2")]
