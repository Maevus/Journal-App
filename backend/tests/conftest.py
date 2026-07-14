"""Shared test fixtures.

Tests run against an in-memory SQLite DB and never touch the real journal.db or
Chroma — `embed_and_store` (the vector-store write) is stubbed out for every test.
"""
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import services.ingestion as ingestion
from models.journal import JournalEntry
from models.usage import LLMUsage  # noqa: F401 — registers the usage table for create_all
from models.user import User  # noqa: F401 — registers the users table for create_all


@pytest.fixture(autouse=True)
def meter_db(monkeypatch):
    """llm_client persists a usage row for every LLM call through its own
    engine — point it at a fresh in-memory DB for every test so metering can
    never write to the real journal.db. Meter tests request this fixture by
    name to assert on the rows."""
    import services.llm_client as llm_client
    import services.quota as quota

    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr(llm_client, "engine", eng)
    # The quota gate (services/quota.py) reads the same meter table the
    # metering writes — both must see the same test DB.
    monkeypatch.setattr(quota, "engine", eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session():
    """A Session on a fresh in-memory SQLite DB with the schema created."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # one shared connection so the in-memory DB persists
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    engine.dispose()


@pytest.fixture(autouse=True)
def _stub_embeddings(monkeypatch):
    """ingest_entries writes to Chroma via embed_and_store; no-op it in tests."""
    monkeypatch.setattr(ingestion, "embed_and_store", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def quota_headroom(monkeypatch):
    """The shipped default budget is $0 (the keyless launch posture), which
    would refuse every attributed LLM call in the suite. Give tests generous
    headroom so quota only bites where a test explicitly sets a budget —
    the same opt-in stance as meter_db."""
    import config

    monkeypatch.setattr(config, "FREE_TIER_DAILY_USD", 100.0)
    monkeypatch.setattr(config, "DEMO_DAILY_USD", 100.0)
    monkeypatch.setattr(config, "GLOBAL_DAILY_USD", 10_000.0)


@pytest.fixture
def client(monkeypatch):
    """A FastAPI TestClient backed by a fresh in-memory DB.

    `get_session` is overridden so endpoints use the test DB; the TestClient is
    used without a `with` block so the app's lifespan (startup Keep sync) never
    runs. `client.engine` is exposed for seeding via the `add_entry` fixture.
    """
    from fastapi.testclient import TestClient

    import main
    import models.journal as journal

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    def _override_session():
        with Session(engine) as s:
            yield s

    monkeypatch.setitem(main.app.dependency_overrides, journal.get_session, _override_session)
    test_client = TestClient(main.app)
    test_client.engine = engine
    yield test_client
    engine.dispose()


@pytest.fixture
def login(client):
    """Log a user into the TestClient: create the user + a session row in the
    client's DB and plant the raw token in the cookie jar. Returns the User."""
    from models.user import User as U
    from services.auth import SESSION_COOKIE, create_session

    def _login(email="user@example.com", google_sub=None):
        # expire_on_commit=False: the returned User is read after the session
        # closes (tests assert on user.id) — don't let commit expire it.
        with Session(client.engine, expire_on_commit=False) as s:
            user = U(google_sub=google_sub or f"sub-{email}", email=email)
            s.add(user)
            s.commit()
            s.refresh(user)
            raw_token = create_session(s, user)
        client.cookies.set(SESSION_COOKIE, raw_token)
        return user

    return _login


@pytest.fixture
def me(login):
    """The default logged-in user. Now every data route requires auth and reads
    are user-scoped, route tests depend on this (directly, or via add_entry)."""
    return login()


# Sentinel default for add_entry's user_id: "owned by `me`". Distinct from None,
# which explicitly means a legacy unowned row.
_OWNED_BY_ME = object()


@pytest.fixture
def add_entry(client, request):
    """Insert a JournalEntry into the client's DB. Returns the persisted row.

    Owned by `me` by default (lazily logging the client in), since scoped reads
    only see the logged-in user's rows. Pass user_id=None for a legacy unowned
    row, or another user's id for cross-user isolation tests.
    """
    def _add(content, *, user_id=_OWNED_BY_ME, source="text", mood_score=None,
             mood_reasoning=None, timestamp=None, external_id=None):
        if user_id is _OWNED_BY_ME:
            user_id = request.getfixturevalue("me").id
        with Session(client.engine) as s:
            entry = JournalEntry(
                user_id=user_id,
                source=source,
                content=content,
                content_hash=ingestion.compute_hash(content),
                mood_score=mood_score,
                mood_reasoning=mood_reasoning,
                external_id=external_id,
                **({"timestamp": timestamp} if timestamp is not None else {}),
            )
            s.add(entry)
            s.commit()
            s.refresh(entry)
            return entry

    return _add
