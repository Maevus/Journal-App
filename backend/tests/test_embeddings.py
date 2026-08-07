"""Unit tests for the pgvector embeddings wrapper: pure helpers + the
off-Postgres degrade path. Real vector behavior is exercised against a live
Postgres in test_embeddings_integration.py (gated on TEST_DATABASE_URL).
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


def test_delete_user_embeddings_noops_off_postgres(session):
    """`session` is SQLite, which has no entry_embeddings table — account
    deletion must skip it rather than raise."""
    assert emb.delete_user_embeddings(session, user_id=1) == 0
