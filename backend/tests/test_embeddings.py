"""Unit tests for the pgvector embeddings wrapper — pure helpers + the
off-Postgres degrade path (no real Postgres, no model download).

Real vector behavior (embedding + ANN ranking + per-user isolation) is exercised
against a live pgvector Postgres in test_embeddings_integration.py, gated on
TEST_DATABASE_URL.
"""
import services.embeddings as emb


def test_to_pgvector_format():
    # pgvector text input: '[v1,v2,...]', no spaces.
    assert emb._to_pgvector([0.5, -1.0, 2.0]) == "[0.5,-1.0,2.0]"


def test_embed_and_store_noops_off_postgres(monkeypatch):
    """SQLite (tests / the interim demo) has no pgvector, so embed_and_store must
    short-circuit before embedding or touching the DB."""
    monkeypatch.setattr(emb, "_on_postgres", lambda: False)
    embedded = []
    monkeypatch.setattr(emb, "_embed", lambda content: embedded.append(content) or [0.0])

    emb.embed_and_store(1, "hello world", user_id=1)  # must not raise
    assert embedded == []  # never embedded


def test_search_similar_empty_off_postgres(monkeypatch):
    monkeypatch.setattr(emb, "_on_postgres", lambda: False)
    embedded = []
    monkeypatch.setattr(emb, "_embed", lambda content: embedded.append(content) or [0.0])

    assert emb.search_similar("query", user_id=1) == []
    assert embedded == []  # short-circuits before embedding the query
