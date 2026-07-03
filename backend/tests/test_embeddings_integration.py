"""Integration test: a real Chroma round-trip with no mocking of the vector store.

This is the one seam the unit tests stub out — it verifies that embed_and_store
actually persists vectors and that search_similar returns real semantic rankings.
It loads the ONNX embedding model, so it's slower than the unit tests and marked
`integration` (deselect with `pytest -m "not integration"`).
"""
import pytest

import services.embeddings as emb


@pytest.fixture
def chroma(tmp_path, monkeypatch):
    """Point the vector store at a throwaway dir and force a fresh client."""
    monkeypatch.setattr(emb, "CHROMA_PERSIST_DIR", str(tmp_path / "chroma"))
    monkeypatch.setattr(emb, "_collection", None)
    yield
    emb._collection = None  # don't leak this temp collection into other tests


@pytest.mark.integration
def test_embed_and_search_round_trip(chroma):
    emb.embed_and_store(1, "rock climbing at the bouldering gym", user_id=1)
    emb.embed_and_store(2, "baking sourdough bread in the oven", user_id=1)
    emb.embed_and_store(3, "belaying technique and lead climbing falls", user_id=1)

    results = emb.search_similar("climbing", limit=3, user_id=1)

    ids = [doc_id for doc_id, _ in results]
    scores = [score for _, score in results]
    assert set(ids) == {1, 2, 3}                       # everything came back
    assert scores == sorted(scores, reverse=True)      # ranked most→least similar
    assert all(0.0 < s <= 1.0 for s in scores)         # distance→similarity mapping
    assert ids[-1] == 2                                # baking is least related to "climbing"
    assert ids[0] in (1, 3)                            # a climbing entry ranks top


@pytest.mark.integration
def test_search_empty_real_collection(chroma):
    assert emb.search_similar("anything", user_id=1) == []


@pytest.mark.integration
def test_upsert_replaces_in_place(chroma):
    emb.embed_and_store(1, "original text about gardening", user_id=1)
    emb.embed_and_store(1, "updated text about astronomy and telescopes", user_id=1)  # same id

    results = emb.search_similar("stars and planets", limit=5, user_id=1)
    assert len(results) == 1        # upsert replaced, not appended
    assert results[0][0] == 1


@pytest.mark.integration
def test_vector_query_is_isolated_per_user(chroma):
    """The design leans on two real Chroma behaviors: the `where` filter drops
    other users' vectors, and a vector with NO user_id key (unowned) matches no
    filter at all. Verify both against the real store, not a mock."""
    emb.embed_and_store(1, "climbing at the bouldering gym", user_id=1)
    emb.embed_and_store(2, "climbing at the climbing wall", user_id=2)
    emb.embed_and_store(3, "legacy unowned climbing note", user_id=None)

    assert [i for i, _ in emb.search_similar("climbing", limit=5, user_id=1)] == [1]
    assert [i for i, _ in emb.search_similar("climbing", limit=5, user_id=2)] == [2]
