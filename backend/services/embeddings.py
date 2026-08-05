"""Vector store backed by Postgres + pgvector.

Embeddings come from fastembed (all-MiniLM-L6-v2, 384-d, ONNX — no torch) into
the Postgres-only `entry_embeddings` table (kept out of SQLModel.metadata so
SQLite never tries to build a `vector` column). Off Postgres, `embed_and_store`/
`search_similar` no-op / return empty.
"""
import logging
from typing import List, Tuple

from sqlalchemy import text

import config
from models.journal import engine

logger = logging.getLogger(__name__)

# 384-d, chosen to match prior stored embeddings' semantics. Downloaded/cached
# on first use — bake into the deploy image to avoid a cold-start fetch.
_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
_embedder = None

# Every search filters by user_id, but the HNSW index covers `embedding` alone:
# a plain scan walks the globally nearest vectors and the filter discards the
# rest, so a user outside that candidate set gets too few rows (or none) — a
# recall bug invisible on small tables, appearing only once the corpus grows
# enough to favor the ANN plan over a btree scan. Iterative scan (pgvector
# >= 0.8) fixes this by scanning until `limit` rows survive the filter;
# `strict_order` (not `relaxed_order`) keeps results exactly most-similar-first,
# since hybrid_search fuses on these scores. Requires pgvector >= 0.8 — older
# extensions raise here instead of silently degrading.
_ITERATIVE_SCAN = "strict_order"


def _on_postgres() -> bool:
    return engine.dialect.name == "postgresql"


def _get_embedder():
    global _embedder
    if _embedder is None:
        from fastembed import TextEmbedding

        # Pin the cache when configured; fastembed otherwise defaults to the
        # system temp dir, which a cold start wipes (see config.FASTEMBED_CACHE_DIR).
        kwargs = {"cache_dir": config.FASTEMBED_CACHE_DIR} if config.FASTEMBED_CACHE_DIR else {}
        _embedder = TextEmbedding(_MODEL_NAME, **kwargs)
    return _embedder


def _embed(content: str) -> List[float]:
    vec = next(iter(_get_embedder().embed([content])))
    return [float(x) for x in vec]  # numpy float32 → JSON/pgvector-friendly floats


def _to_pgvector(vec: List[float]) -> str:
    """pgvector's text input format: '[v1,v2,...]'. Bound as a string and cast
    `::vector` in SQL, so no per-connection type registration is needed."""
    return "[" + ",".join(repr(x) for x in vec) + "]"


def embed_and_store(entry_id: int, content: str, *, user_id: int) -> None:
    """Embed one entry and upsert it into the vector store, owner-stamped.

    `user_id` is required — every entry has an owner, so there are no unowned
    vectors. No-ops off Postgres.
    """
    if not _on_postgres():
        return
    emb = _to_pgvector(_embed(content))
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO entry_embeddings (entry_id, user_id, embedding) "
                "VALUES (:eid, :uid, (:emb)::vector) "
                "ON CONFLICT (entry_id) DO UPDATE SET "
                "user_id = EXCLUDED.user_id, embedding = EXCLUDED.embedding"
            ),
            {"eid": entry_id, "uid": user_id, "emb": emb},
        )


def search_similar(query: str, limit: int = 10, *, user_id: int) -> List[Tuple[int, float]]:
    """Return [(entry_id, similarity)] of `user_id`'s entries, most similar first.

    Similarity is cosine (`1 - (embedding <=> q)`), ~[0, 1] over the normalized
    MiniLM space. `user_id` is required and keyword-only so an unscoped query is
    inexpressible and can't be transposed with `limit` (ADR 0003). The
    `WHERE user_id` filter is defense in depth — SQL is the isolation boundary
    of record in `hybrid_search`. Empty off Postgres.
    """
    if not _on_postgres():
        return []
    q = _to_pgvector(_embed(query))
    # A transaction purely so SET LOCAL is scoped to this query and reverts on
    # exit — the connection goes back to the pool with no setting left on it.
    with engine.begin() as conn:
        conn.execute(text(f"SET LOCAL hnsw.iterative_scan = {_ITERATIVE_SCAN}"))
        rows = conn.execute(
            text(
                "SELECT entry_id, 1 - (embedding <=> (:q)::vector) AS similarity "
                "FROM entry_embeddings WHERE user_id = :uid "
                "ORDER BY embedding <=> (:q)::vector LIMIT :n"
            ),
            {"q": q, "uid": user_id, "n": limit},
        ).all()
    return [(int(eid), float(sim)) for eid, sim in rows]
