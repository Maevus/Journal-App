"""Vector store backed by Postgres + pgvector.

Embeddings are generated locally with fastembed (all-MiniLM-L6-v2, 384-d, ONNX —
no torch) and stored in the Postgres-only `entry_embeddings` table (created by the
Alembic migration, kept out of SQLModel.metadata so SQLite never tries to build a
`vector` column). Off Postgres — the SQLite unit suite and the interim SQLite demo
— the store degrades to a no-op / empty result: those paths either mock at the
retrieval boundary or simply have no semantic layer yet (the demo regains it when
it moves onto Postgres as a seed user).

The public interface is unchanged from the Chroma version — `embed_and_store` +
`search_similar` with the same signatures and the same `[(entry_id, similarity)]`
return — so `retrieval.hybrid_search` and `ingestion` need no changes.
"""
import logging
from typing import List, Tuple

from sqlalchemy import text

from models.journal import engine

logger = logging.getLogger(__name__)

# 384-d, matches the model Chroma's DefaultEmbeddingFunction used, so semantics
# carry over. The model is downloaded/cached on first use (bake it into the
# deploy image to avoid a cold-start fetch).
_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
_embedder = None


def _on_postgres() -> bool:
    return engine.dialect.name == "postgresql"


def _get_embedder():
    global _embedder
    if _embedder is None:
        from fastembed import TextEmbedding

        _embedder = TextEmbedding(_MODEL_NAME)
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

    `user_id` is required and non-null — every entry has an owner since the
    Postgres migration, so there are no unowned vectors. No-ops off Postgres
    (SQLite tests / the interim demo have no pgvector).
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

    Similarity is cosine (`1 - (embedding <=> q)`, pgvector's cosine distance),
    ~[0, 1] over the normalized MiniLM space. `user_id` is required and
    keyword-only: an unscoped ANN query over a journal must be inexpressible, and
    a transposed arg can't bind to the adjacent int `limit` (ADR 0003). The
    `WHERE user_id` filter is defense in depth — SQL stays the isolation boundary
    of record in `hybrid_search`. Empty off Postgres.
    """
    if not _on_postgres():
        return []
    q = _to_pgvector(_embed(query))
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT entry_id, 1 - (embedding <=> (:q)::vector) AS similarity "
                "FROM entry_embeddings WHERE user_id = :uid "
                "ORDER BY embedding <=> (:q)::vector LIMIT :n"
            ),
            {"q": q, "uid": user_id, "n": limit},
        ).all()
    return [(int(eid), float(sim)) for eid, sim in rows]
