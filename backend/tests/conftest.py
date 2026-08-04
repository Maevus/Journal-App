"""Shared test fixtures.

Tests run against an in-memory SQLite DB and never touch the real journal.db or
the pgvector store — `embed_and_store` (the vector-store write) is stubbed out for
every test (and no-ops off Postgres anyway).
"""
import os
from urllib.parse import urlparse

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import services.ingestion as ingestion
from models.journal import JournalEntry
from models.keys import UserApiKey  # noqa: F401 — registers the key table for create_all
from models.rate_limit import RateLimitCounter  # noqa: F401 — registers the counter table
from models.usage import LLMUsage  # noqa: F401 — registers the usage table for create_all
from models.user import User  # noqa: F401 — registers the users table for create_all

TEST_DB_SUFFIX = "_test"


def require_test_database_url() -> str:
    """`TEST_DATABASE_URL`, refusing anything that isn't obviously a scratch DB.

    The Postgres-backed tests run `alembic downgrade base`, which drops every
    table on the target. Harmless on a throwaway database, destroys a real
    journal — and the two are one environment variable apart. Opt in explicitly
    by giving the database a `_test` suffix.
    """
    url = os.environ["TEST_DATABASE_URL"]
    name = urlparse(url).path.lstrip("/")
    if not name.endswith(TEST_DB_SUFFIX):
        raise RuntimeError(
            f"Refusing to run: TEST_DATABASE_URL names the database {name!r}, which "
            f"lacks the {TEST_DB_SUFFIX!r} suffix. These tests drop every table on the "
            "target. Point them at a scratch database instead:\n"
            "  docker exec journal-postgres createdb -U journal journal_test\n"
            "  TEST_DATABASE_URL=postgresql+psycopg://journal:journal@localhost:5432/"
            "journal_test .venv/bin/pytest"
        )
    return url


@pytest.fixture(autouse=True)
def insecure_cookies(monkeypatch):
    """`config.COOKIE_SECURE` defaults on, which is right in production and wrong
    for TestClient: it speaks plain http, so httpx drops a Secure cookie and every
    follow-up request arrives unauthenticated. Turn it off for the suite; the one
    test that asserts the production behaviour re-enables it explicitly."""
    import config

    monkeypatch.setattr(config, "COOKIE_SECURE", False)


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
    """ingest_entries writes to the vector store via embed_and_store; no-op it."""
    monkeypatch.setattr(ingestion, "embed_and_store", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _reset_demo_user_cache():
    """services.auth caches the demo user's id (a per-process constant in prod).
    Each test uses a fresh DB, so clear it around every test or a stale id leaks
    across tests."""
    import services.auth as auth

    auth._demo_user_id = None
    yield
    auth._demo_user_id = None


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


@pytest.fixture(autouse=True)
def rate_limit_headroom(monkeypatch):
    """The shipped limits are tight on purpose (chat is 5/min), which several
    route tests would trip just by making a handful of requests. Give every tier
    generous headroom so the limiter only bites where a test sets a budget
    explicitly — the same opt-in stance `quota_headroom` takes, and for the same
    reason: a test failing on a limit it never mentioned is a mystery.

    Headroom rather than 0 (which disables a window entirely), so the limiter's
    real code path — the upsert, the decision — still runs under every route
    test rather than being switched off for the whole suite.
    """
    import config

    for tier in ("CHAT", "SYNTHESIS", "INGEST", "KEYS", "SEARCH", "AUTH"):
        monkeypatch.setattr(config, f"RATE_LIMIT_{tier}_PER_MIN", 10_000)
        monkeypatch.setattr(config, f"RATE_LIMIT_{tier}_PER_HOUR", 10_000)


@pytest.fixture(autouse=True)
def _frozen_rate_limit_clock(monkeypatch):
    """Pin the limiter's clock for the suite.

    The windows are floored to the wall clock, so a test that sends two requests
    expecting the second to be refused fails whenever the pair straddles a minute
    boundary — roughly once every few hundred runs, and only ever on someone
    else's machine. Freezing it makes the boundary a thing tests choose (by
    passing `now` explicitly) rather than a thing they race.

    Scoped to `services.rate_limit` only: session expiry and the quota gate read
    their own `utcnow_naive`, and those tests want the real clock.
    """
    import services.rate_limit as rate_limit
    from datetime import datetime

    frozen = datetime(2026, 8, 4, 10, 30, 12)
    monkeypatch.setattr(rate_limit, "utcnow_naive", lambda: frozen)


@pytest.fixture(autouse=True)
def _reset_rate_limit_sweep():
    """The counter sweep is throttled by a module-level timestamp (a per-process
    constant in prod). Tests inject their own `now`, so a stamp left by one test
    would decide whether another's sweep runs — clear it around each."""
    import services.rate_limit as rate_limit

    rate_limit._last_swept = None
    yield
    rate_limit._last_swept = None


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


# Sentinel default for add_entry's user_id: "owned by `me`".
_OWNED_BY_ME = object()


@pytest.fixture
def add_entry(client, request):
    """Insert a JournalEntry into the client's DB. Returns the persisted row.

    Owned by `me` by default (lazily logging the client in), since scoped reads
    only see the logged-in user's rows. Pass another user's id for cross-user
    isolation tests. `user_id` is NOT NULL since the Postgres migration, so an
    ownerless row is no longer a valid state.
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
