"""Integration test: a real fastembed + pgvector round-trip, no mocking.

Gated on TEST_DATABASE_URL (a pgvector-enabled Postgres), like the migration
smoke tests — skipped in the default SQLite suite. Verifies that embed_and_store
persists real vectors and search_similar returns real semantic rankings, scoped
per user. Loads the ONNX embedding model, so it's slower than the unit tests.
"""
import os
from pathlib import Path

import pytest
from sqlmodel import Session, create_engine

from alembic import command
from alembic.config import Config

import services.embeddings as emb
from conftest import require_test_database_url
from models.journal import JournalEntry
from models.user import User

_BACKEND = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="set TEST_DATABASE_URL to a scratch pgvector Postgres DB (name must end in "
    "`_test`) to run the embeddings integration tests — they drop every table on the target",
)


def _alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_BACKEND / "alembic"))
    # % is ConfigParser interpolation syntax — escape it so a URL-encoded
    # password doesn't raise. get_main_option un-escapes on read.
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


@pytest.fixture
def pg(monkeypatch):
    """Fresh pgvector schema on the test DB, two users to own entries, and
    emb.engine pointed at it. Cleans the schema down afterward."""
    url = require_test_database_url()
    cfg = _alembic_config(url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")  # builds all tables incl. entry_embeddings + the extension

    engine = create_engine(url)
    with Session(engine) as s:
        s.add_all([User(google_sub="a", email="a@x"), User(google_sub="b", email="b@x")])
        s.commit()
    monkeypatch.setattr(emb, "engine", engine)
    yield engine
    engine.dispose()
    command.downgrade(cfg, "base")


def _store(engine, entry_id: int, user_id: int, content: str) -> None:
    """entry_embeddings FKs journalentry+users, so the entry must exist first."""
    with Session(engine) as s:
        s.add(JournalEntry(
            id=entry_id, user_id=user_id, source="text",
            content=content, content_hash=f"h{entry_id}",
        ))
        s.commit()
    emb.embed_and_store(entry_id, content, user_id=user_id)


def test_embed_and_search_round_trip(pg):
    _store(pg, 1, 1, "rock climbing at the bouldering gym")
    _store(pg, 2, 1, "baking sourdough bread in the oven")
    _store(pg, 3, 1, "belaying technique and lead climbing falls")

    results = emb.search_similar("climbing", limit=3, user_id=1)
    ids = [i for i, _ in results]
    scores = [s for _, s in results]

    assert set(ids) == {1, 2, 3}                    # everything came back
    assert scores == sorted(scores, reverse=True)   # ranked most→least similar
    assert all(s <= 1.0 for s in scores)            # cosine similarity upper bound
    assert ids[-1] == 2                             # baking least related to "climbing"
    assert ids[0] in (1, 3)                         # a climbing entry ranks top


def test_search_empty(pg):
    assert emb.search_similar("anything", user_id=1) == []


def test_upsert_replaces_in_place(pg):
    _store(pg, 1, 1, "original text about gardening")
    emb.embed_and_store(1, "updated text about astronomy and telescopes", user_id=1)  # same id

    results = emb.search_similar("stars and planets", limit=5, user_id=1)
    assert len(results) == 1        # upsert replaced, not appended
    assert results[0][0] == 1


def test_vector_query_is_isolated_per_user(pg):
    _store(pg, 1, 1, "climbing at the bouldering gym")
    _store(pg, 2, 2, "climbing at the climbing wall")

    assert [i for i, _ in emb.search_similar("climbing", limit=5, user_id=1)] == [1]
    assert [i for i, _ in emb.search_similar("climbing", limit=5, user_id=2)] == [2]
