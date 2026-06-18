"""Tests for the schema migration and the mood-scoring write-back."""
import sqlite3

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import models.journal as journal
import services.ingestion as ingestion
from models.journal import JournalEntry


def test_ensure_schema_adds_missing_column(monkeypatch, tmp_path):
    db = tmp_path / "old.db"
    # Simulate a DB created before mood_reasoning existed.
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE journalentry (id INTEGER PRIMARY KEY, content TEXT, content_hash TEXT)")
    con.commit()
    con.close()

    eng = create_engine(f"sqlite:///{db}")
    monkeypatch.setattr(journal, "engine", eng)
    journal.ensure_schema()
    journal.ensure_schema()  # idempotent — must not error or duplicate
    eng.dispose()

    con = sqlite3.connect(db)
    cols = [r[1] for r in con.execute("PRAGMA table_info(journalentry)")]
    con.close()
    assert "mood_reasoning" in cols


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
        e = JournalEntry(source="text", content="x", content_hash="h-" + str(id(engine)))
        s.add(e)
        s.commit()
        s.refresh(e)
        return e.id


async def test_score_mood_writes_and_clamps(mood_engine, monkeypatch):
    eid = _make_entry(mood_engine)

    async def fake(*args, **kwargs):
        return ingestion._MoodScore(score=1.5, reasoning="very upbeat")

    monkeypatch.setattr("services.llm_client.structured_output", fake)
    await ingestion.score_mood_background(eid, "x")

    with Session(mood_engine) as s:
        e = s.get(JournalEntry, eid)
        assert e.mood_score == 1.0  # clamped from 1.5
        assert e.mood_reasoning == "very upbeat"


async def test_score_mood_swallows_errors(mood_engine, monkeypatch):
    eid = _make_entry(mood_engine)

    async def boom(*args, **kwargs):
        raise RuntimeError("api down")

    monkeypatch.setattr("services.llm_client.structured_output", boom)
    await ingestion.score_mood_background(eid, "x")  # must not raise

    with Session(mood_engine) as s:
        e = s.get(JournalEntry, eid)
        assert e.mood_score is None
        assert e.mood_reasoning is None
