"""Shared test fixtures.

Tests run against an in-memory SQLite DB and never touch the real journal.db or
Chroma — `embed_and_store` (the vector-store write) is stubbed out for every test.
"""
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import services.ingestion as ingestion
from models.journal import JournalEntry
from models.user import User  # noqa: F401 — registers the users table for create_all


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
def add_entry(client):
    """Insert a JournalEntry into the client's DB. Returns the persisted row."""
    def _add(content, *, source="text", mood_score=None, mood_reasoning=None,
             timestamp=None, external_id=None):
        with Session(client.engine) as s:
            entry = JournalEntry(
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
