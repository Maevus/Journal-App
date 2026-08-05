"""Tests for the mood-scoring write-back.

Schema migration itself is Alembic's — see test_migrations.py.
"""
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import services.ingestion as ingestion
from models.journal import JournalEntry


@pytest.fixture
def mood_engine(monkeypatch):
    """In-memory engine wired into the ingestion module (score_mood_background
    uses the module-level `engine`)."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(ingestion, "engine", engine)
    yield engine
    engine.dispose()


def _make_entry(engine) -> int:
    with Session(engine) as s:
        # user_id is NOT NULL since the Postgres migration; SQLite doesn't
        # enforce the FK, so a bare owner id is enough for these unit tests.
        e = JournalEntry(
            source="text", content="x", content_hash="h-" + str(id(engine)), user_id=1
        )
        s.add(e)
        s.commit()
        s.refresh(e)
        return e.id


async def test_score_mood_writes_and_clamps(mood_engine, monkeypatch):
    eid = _make_entry(mood_engine)

    async def fake(*args, **kwargs):
        return ingestion._MoodScore(score=1.5, reasoning="very upbeat")

    monkeypatch.setattr("services.llm_client.structured_output", fake)
    await ingestion.score_mood_background(eid, "x", user_id=1)

    with Session(mood_engine) as s:
        e = s.get(JournalEntry, eid)
        assert e.mood_score == 1.0  # clamped from 1.5
        assert e.mood_reasoning == "very upbeat"


async def test_score_mood_swallows_errors(mood_engine, monkeypatch):
    eid = _make_entry(mood_engine)

    async def boom(*args, **kwargs):
        raise RuntimeError("api down")

    monkeypatch.setattr("services.llm_client.structured_output", boom)
    await ingestion.score_mood_background(eid, "x", user_id=1)  # must not raise

    with Session(mood_engine) as s:
        e = s.get(JournalEntry, eid)
        assert e.mood_score is None
        assert e.mood_reasoning is None


async def test_score_mood_skips_unowned_rows_without_calling_llm(
    mood_engine, monkeypatch, caplog
):
    """An unowned row has no owner to bill, so mood scoring must not run at
    all (F3): the LLM is never called, and the skip logs at INFO, not as a
    failure."""
    eid = _make_entry(mood_engine)
    called = False

    async def must_not_run(*args, **kwargs):
        nonlocal called
        called = True
        return ingestion._MoodScore(score=0.5, reasoning="nope")

    monkeypatch.setattr("services.llm_client.structured_output", must_not_run)
    with caplog.at_level("INFO", logger="services.ingestion"):
        await ingestion.score_mood_background(eid, "x", user_id=None)

    assert called is False  # no owner → no LLM call → no spend
    with Session(mood_engine) as s:
        assert s.get(JournalEntry, eid).mood_score is None
    assert not any(r.levelname == "WARNING" for r in caplog.records)  # skip, not failure


async def test_score_mood_quota_skip_is_logged_as_skip_not_failure(
    mood_engine, monkeypatch, caplog
):
    """A quota refusal is expected (F2), not an error: nothing raises, and the
    log says 'quota' at INFO, distinct from a 'failed' WARNING."""
    from services.quota import QuotaExceededError

    eid = _make_entry(mood_engine)

    async def refuse(*args, **kwargs):
        raise QuotaExceededError("daily LLM budget exhausted")

    monkeypatch.setattr("services.llm_client.structured_output", refuse)
    with caplog.at_level("INFO", logger="services.ingestion"):
        await ingestion.score_mood_background(eid, "x", user_id=1)  # must not raise

    assert any(
        "quota" in r.message.lower() and r.levelname == "INFO" for r in caplog.records
    )
    assert not any(r.levelname == "WARNING" for r in caplog.records)

    with Session(mood_engine) as s:
        assert s.get(JournalEntry, eid).mood_score is None
