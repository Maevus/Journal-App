"""ANN recall when the vector index is crowded by other users' entries.

Postgres-only (gated on TEST_DATABASE_URL) — SQLite has no ANN index.

The bug this guards: `search_similar` filters `WHERE user_id`, but the HNSW
index covers `embedding` alone, so a crowded ANN scan can return only other
users' nearest vectors — an empty result indistinguishable from "nothing
matched". Small tables mask it (planner prefers a btree scan); it surfaces
once the table is large enough that the planner prefers the ANN plan.

Tests force that plan at 200 rows via `enable_sort=off` + a small
`hnsw.ef_search`, reproducing what production does organically at scale.
"""
import os
from pathlib import Path

import pytest
from sqlalchemy import text
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
    "`_test`) to run the ANN filter tests — they drop every table on the target",
)

_DIMS = 384
# Two poles: user 1's entries sit on NEAR (owns the query neighbourhood);
# user 2's sit on FAR, unreachable unless the scan looks past first candidates.
_NEAR = [1.0] + [0.0] * (_DIMS - 1)
_FAR = [0.0] * (_DIMS - 1) + [1.0]


def _alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


@pytest.fixture
def pg_ann(monkeypatch):
    """Schema on Postgres, connections pinned to force the ANN plan (see module
    docstring) at just 200 rows."""
    url = require_test_database_url()
    cfg = _alembic_config(url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    engine = create_engine(
        url,
        connect_args={"options": "-c enable_sort=off -c enable_seqscan=off -c hnsw.ef_search=10"},
    )
    with Session(engine) as s:
        s.add_all([User(google_sub="a", email="a@x"), User(google_sub="b", email="b@x")])
        s.commit()
    monkeypatch.setattr(emb, "engine", engine)
    yield engine
    engine.dispose()
    command.downgrade(cfg, "base")


def _seed(engine, *, entry_id: int, user_id: int, vec: list[float]) -> None:
    """Insert an entry and its vector directly, bypassing fastembed — these
    tests are about index geometry, not embedding quality."""
    with Session(engine) as s:
        s.add(JournalEntry(
            id=entry_id, user_id=user_id, source="text",
            content=f"entry {entry_id}", content_hash=f"h{entry_id}",
        ))
        s.commit()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO entry_embeddings (entry_id, user_id, embedding) "
                "VALUES (:id, :uid, (:v)::vector)"
            ),
            {"id": entry_id, "uid": user_id, "v": emb._to_pgvector(vec)},
        )


def test_a_crowded_index_still_returns_your_own_entries(pg_ann, monkeypatch):
    """One user's entries filling the candidate set must not leave another
    user's search empty — 200 of user 1's entries sit on the query; user 2's
    five sit at the opposite pole and must still come back.
    """
    monkeypatch.setattr(emb, "_embed", lambda _content: _NEAR)

    for entry_id in range(1, 201):
        _seed(pg_ann, entry_id=entry_id, user_id=1, vec=_NEAR)
    for entry_id in range(201, 206):
        _seed(pg_ann, entry_id=entry_id, user_id=2, vec=_FAR)

    results = emb.search_similar("anything", limit=5, user_id=2)

    # Compared as a set: identical vectors tie on distance, so no order among
    # them is "more correct". Ranking itself is pinned by the next test.
    assert {entry_id for entry_id, _ in results} == {201, 202, 203, 204, 205}


def test_the_crowding_user_is_unaffected(pg_ann, monkeypatch):
    """The other side of the same fix: whatever makes the filtered case correct
    must not cost the user whose entries the index is full of."""
    monkeypatch.setattr(emb, "_embed", lambda _content: _NEAR)

    for entry_id in range(1, 201):
        _seed(pg_ann, entry_id=entry_id, user_id=1, vec=_NEAR)
    for entry_id in range(201, 206):
        _seed(pg_ann, entry_id=entry_id, user_id=2, vec=_FAR)

    results = emb.search_similar("anything", limit=5, user_id=1)

    assert len(results) == 5
    assert all(entry_id < 201 for entry_id, _ in results)


def test_results_stay_in_distance_order(pg_ann, monkeypatch):
    """`search_similar` promises most-similar-first; the fix must not relax
    that to approximately-ordered — hybrid_search fuses on these scores.
    """
    monkeypatch.setattr(emb, "_embed", lambda _content: _NEAR)

    # A spread of distances for user 2, plus user-1 noise crowding the index.
    for entry_id in range(1, 201):
        _seed(pg_ann, entry_id=entry_id, user_id=1, vec=_NEAR)
    for offset, entry_id in enumerate(range(201, 211)):
        vec = [1.0 - offset * 0.1] + [0.0] * (_DIMS - 2) + [offset * 0.1]
        _seed(pg_ann, entry_id=entry_id, user_id=2, vec=vec)

    results = emb.search_similar("anything", limit=10, user_id=2)
    scores = [score for _, score in results]

    assert len(results) == 10
    assert scores == sorted(scores, reverse=True)
