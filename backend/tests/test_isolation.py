"""Per-user isolation — schema + per-user dedup (ADR 0003).

Identical text from two users hashes identically, so a global content-hash
dedup would silently drop the second user's entry — the dedup key is
composite (user_id, content_hash).
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


def test_ownerless_ingest_is_skipped(session):
    """Startup Keep/GoodNotes sync runs outside a request; user_id is NOT
    NULL, so an unresolvable owner (not yet logged in) ingests nothing rather
    than leaving an orphaned NULL-owner row."""
    stats, entries = ingest_entries(["Legacy note."], "text", session)

    assert entries == []
    assert stats == {"ingested": 0, "updated": 0, "skipped": 1}
    assert session.exec(select(JournalEntry)).all() == []


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


def test_demo_user_cannot_ingest(client, monkeypatch):
    """The demo is read-only: a signed-out visitor (resolved to the demo user
    under PUBLIC_DEMO) is refused writes, so one visitor can't pollute the next."""
    import config

    monkeypatch.setattr(config, "PUBLIC_DEMO", True)

    resp = client.post("/ingest", json={"entries": ["hi"], "source": "text"})

    assert resp.status_code == 403
    with Session(client.engine) as s:
        assert s.exec(select(JournalEntry)).all() == []  # nothing written


def test_sync_owner_resolves_by_google_email(session, monkeypatch):
    """Keep/GoodNotes are env-configured for one Google account (GOOGLE_EMAIL,
    pre-multi-user legacy); entries belong to that user once it exists, else
    stay unowned."""
    from models.user import User
    from services.ingestion import _resolve_sync_owner

    monkeypatch.setenv("GOOGLE_EMAIL", "owner@example.com")
    assert _resolve_sync_owner(session) is None  # never logged in → unowned

    user = User(google_sub="s", email="owner@example.com")
    session.add(user)
    session.commit()
    session.refresh(user)
    assert _resolve_sync_owner(session) == user.id


# --- read-path isolation (slice 3): every read is gated and user-scoped ---

# Every data-reading route, with a valid request shape — so a failure is the
# auth gate (401), never validation (422).
READ_ROUTES = [
    ("get", "/entries", {"params": {"date": "2026-05-10"}}),
    ("get", "/trends", {"params": {"days": 30}}),
    ("post", "/search", {"json": {"query": "anything", "limit": 5}}),
    ("get", "/export", {"params": {"format": "json"}}),
    ("post", "/stream/synthesis", {"json": {"days": 0, "limit": 5}}),
    ("post", "/stream/chat", {"json": {"messages": [{"role": "user", "content": "hi"}]}}),
]


@pytest.mark.parametrize("method,path,kwargs", READ_ROUTES, ids=[r[1] for r in READ_ROUTES])
def test_read_routes_require_login(client, method, path, kwargs):
    assert getattr(client, method)(path, **kwargs).status_code == 401


def test_entries_show_only_your_own(client, login, add_entry):
    from datetime import datetime

    alice = login("alice@example.com")
    add_entry("alice private", user_id=alice.id, timestamp=datetime(2026, 5, 10, 12))
    bob = login("bob@example.com")  # second login: the client's cookie is now bob's
    add_entry("bob note", user_id=bob.id, timestamp=datetime(2026, 5, 10, 13))

    entries = client.get("/entries", params={"date": "2026-05-10"}).json()["entries"]
    assert [e["content"] for e in entries] == ["bob note"]


def test_trends_average_only_your_scores(client, login, add_entry):
    from datetime import datetime

    alice = login("alice@example.com")
    add_entry("alice day", user_id=alice.id, mood_score=0.9, timestamp=datetime(2026, 5, 10))
    bob = login("bob@example.com")
    add_entry("bob day", user_id=bob.id, mood_score=0.1, timestamp=datetime(2026, 5, 10))

    trends = client.get("/trends", params={"days": 0}).json()["trends"]
    assert trends == [{"date": "2026-05-10", "score": 0.1}]  # bob's average, untouched by alice's 0.9


def test_export_contains_only_your_rows(client, login, add_entry):
    alice = login("alice@example.com")
    add_entry("alice secret", user_id=alice.id)
    bob = login("bob@example.com")
    add_entry("bob note", user_id=bob.id)

    resp = client.get("/export", params={"format": "json"})
    contents = [row["content"] for row in resp.json()]
    assert contents == ["bob note"]


def test_search_cannot_surface_another_users_entry(client, login, add_entry, monkeypatch):
    """Even a perfect semantic hit on someone else's entry must not leak:
    the SQL candidate set is scoped, and fusion drops out-of-scope ids."""
    import services.retrieval as retrieval

    alice = login("alice@example.com")
    secret = add_entry("alice secret climbing trip", user_id=alice.id)
    bob = login("bob@example.com")
    add_entry("bob cooking dinner", user_id=bob.id)
    monkeypatch.setattr(retrieval, "search_similar", lambda q, n, *, user_id=None: [(secret.id, 1.0)])

    results = client.post("/search", json={"query": "climbing", "limit": 5}).json()["results"]
    assert secret.id not in [r["id"] for r in results]


def test_synthesis_prompt_only_sees_your_entries(client, login, add_entry, monkeypatch):
    """The LLM prompt is the leak surface for synthesis — assert on what gets
    sent, not just what comes back."""
    from collections import OrderedDict
    from datetime import datetime

    import routers.synthesis as synthesis

    monkeypatch.setattr(synthesis, "_CACHE", OrderedDict())
    seen = {}

    async def fake_stream(*args, **kwargs):
        seen["prompt"] = kwargs["prompt"]
        yield "ok"

    monkeypatch.setattr(synthesis, "stream_text", fake_stream)
    alice = login("alice@example.com")
    add_entry("ALICE_SECRET", user_id=alice.id, timestamp=datetime(2026, 5, 10))
    bob = login("bob@example.com")
    add_entry("BOB_NOTE", user_id=bob.id, timestamp=datetime(2026, 5, 11))

    r = client.post("/stream/synthesis", json={"days": 0, "limit": 5})

    assert r.status_code == 200
    assert "BOB_NOTE" in seen["prompt"]
    assert "ALICE_SECRET" not in seen["prompt"]


def test_chat_retrieval_is_scoped_to_the_logged_in_user(client, login, monkeypatch):
    """/chat delegates retrieval to hybrid_search — the contract is that it
    passes the logged-in user's id through."""
    import routers.chat as chat

    seen = {}

    def fake_hybrid(session, query, limit, start=None, end=None, *, user_id):
        seen["user_id"] = user_id
        return []

    async def fake_condense(history, question, *, user_id=None):
        return question

    async def fake_parse(question, **kwargs):
        return (None, None)

    async def fake_stream(*args, **kwargs):
        yield "x"

    monkeypatch.setattr(chat, "hybrid_search", fake_hybrid)
    monkeypatch.setattr(chat, "condense_query", fake_condense)
    monkeypatch.setattr(chat, "parse_date_range", fake_parse)
    monkeypatch.setattr(chat, "stream_text", fake_stream)
    bob = login("bob@example.com")

    r = client.post("/stream/chat", json={"messages": [{"role": "user", "content": "q"}]})

    assert r.status_code == 200
    assert seen["user_id"] == bob.id


def test_hybrid_search_is_scoped_to_the_user(session, monkeypatch):
    """Service-level: the SQL candidate set is the isolation boundary — a
    top-ranked semantic hit on another user's entry is dropped at fusion."""
    import services.retrieval as retrieval

    _, [alice_entry] = ingest_entries(["climbing rocks"], "text", session, user_id=1)
    _, [bob_entry] = ingest_entries(["climbing walls"], "text", session, user_id=2)
    monkeypatch.setattr(
        retrieval, "search_similar",
        lambda q, n, *, user_id=None: [(alice_entry.id, 1.0), (bob_entry.id, 0.9)],
    )

    hits = retrieval.hybrid_search(session, "climbing", 5, user_id=2)
    assert [e.id for e, _ in hits] == [bob_entry.id]


def test_retrieval_scopes_the_vector_query_itself(session, monkeypatch):
    """Defense in depth: hybrid_search must pass user_id into the pgvector
    query too, not just drop foreign ids at fusion — so the vector store
    enforces isolation as well."""
    import services.retrieval as retrieval

    ingest_entries(["climbing rocks"], "text", session, user_id=2)
    seen = {}

    def fake_search_similar(query, limit=10, *, user_id=None):
        seen["user_id"] = user_id
        return []

    monkeypatch.setattr(retrieval, "search_similar", fake_search_similar)
    retrieval.hybrid_search(session, "climbing", 5, user_id=2)
    assert seen["user_id"] == 2


def test_source_edit_update_is_user_scoped(session):
    """Same source + external_id from two users must never cross-update:
    the edited-at-source update path has to match within the owner only."""
    a = RawEntry(content="v1", timestamp=None, external_id="n1")
    b = RawEntry(content="v2", timestamp=None, external_id="n1")
    ingest_entries([a], "google_keep", session, user_id=1)
    ingest_entries([b], "google_keep", session, user_id=2)

    rows = session.exec(select(JournalEntry)).all()
    assert sorted((r.user_id, r.content) for r in rows) == [(1, "v1"), (2, "v2")]
