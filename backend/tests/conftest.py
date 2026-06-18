"""Shared test fixtures.

Tests run against an in-memory SQLite DB and never touch the real journal.db or
Chroma — `embed_and_store` (the vector-store write) is stubbed out for every test.
"""
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import services.ingestion as ingestion


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
