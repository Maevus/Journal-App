"""Per-user isolation — schema + per-user dedup (ADR 0003).

Identical text from two users hashes identically, so a *global*
content-hash dedup would silently drop the second user's entry. The dedup
key is per-user — composite (user_id, content_hash) — keeping the hash a
pure content fingerprint and user_id an orthogonal scoping dimension.
"""
import pytest
from sqlmodel import Session, select

import routers.ingest as ingest_router
from models.journal import JournalEntry
from services.ingestion import RawEntry, ingest_entries


@pytest.fixture(autouse=True)
def _stub_mood_scoring(monkeypatch):
    """Router-level ingests schedule background mood scoring — stub the LLM."""

    async def _noop(*args, **kwargs):
        pass

    monkeypatch.setattr(ingest_router, "score_mood_background", _noop)


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


def test_ingest_requires_login(client):
    resp = client.post("/ingest", json={"entries": ["hi"], "source": "text"})

    assert resp.status_code == 401


def test_ingest_stamps_the_logged_in_user(client, login):
    user = login()
    resp = client.post("/ingest", json={"entries": ["Went climbing."], "source": "text"})

    assert resp.status_code == 200
    with Session(client.engine) as s:
        rows = s.exec(select(JournalEntry)).all()
        assert [r.user_id for r in rows] == [user.id]


def test_demo_mode_auto_logs_in_demo_user(client, monkeypatch):
    """Demo deployments never 401 (decisions.md): get_current_user resolves to
    a seeded demo account instead — same isolation code path, no bypass."""
    import config

    monkeypatch.setattr(config, "IS_DEMO", True)

    resp = client.get("/auth/me")  # no cookie at all

    assert resp.status_code == 200
    assert resp.json()["email"] == "demo@journal.local"


def test_demo_mode_ingest_stamps_the_demo_user(client, monkeypatch):
    import config

    monkeypatch.setattr(config, "IS_DEMO", True)

    resp = client.post("/ingest", json={"entries": ["hi"], "source": "text"})

    assert resp.status_code == 200
    with Session(client.engine) as s:
        [row] = s.exec(select(JournalEntry)).all()
        assert row.user_id is not None  # owned by the auto-created demo user


def test_sync_owner_resolves_by_google_email(session, monkeypatch):
    """The Keep/GoodNotes connectors are env-configured for exactly one Google
    account (GOOGLE_EMAIL) — pre-multi-user legacy. Their entries belong to
    that account's user once it exists; before then they stay unowned."""
    from models.user import User
    from services.ingestion import _resolve_sync_owner

    monkeypatch.setenv("GOOGLE_EMAIL", "owner@example.com")
    assert _resolve_sync_owner(session) is None  # never logged in → unowned

    user = User(google_sub="s", email="owner@example.com")
    session.add(user)
    session.commit()
    session.refresh(user)
    assert _resolve_sync_owner(session) == user.id


def test_source_edit_update_is_user_scoped(session):
    """Same source + external_id from two users must never cross-update:
    the edited-at-source update path has to match within the owner only."""
    a = RawEntry(content="v1", timestamp=None, external_id="n1")
    b = RawEntry(content="v2", timestamp=None, external_id="n1")
    ingest_entries([a], "google_keep", session, user_id=1)
    ingest_entries([b], "google_keep", session, user_id=2)

    rows = session.exec(select(JournalEntry)).all()
    assert sorted((r.user_id, r.content) for r in rows) == [(1, "v1"), (2, "v2")]
