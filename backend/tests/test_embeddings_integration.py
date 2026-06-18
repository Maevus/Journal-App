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
    emb.embed_and_store(1, "rock climbing at the bouldering gym")
    emb.embed_and_store(2, "baking sourdough bread in the oven")
    emb.embed_and_store(3, "belaying technique and lead climbing falls")

    results = emb.search_similar("climbing", limit=3)

    ids = [doc_id for doc_id, _ in results]
    scores = [score for _, score in results]
    assert set(ids) == {1, 2, 3}                       # everything came back
    assert scores == sorted(scores, reverse=True)      # ranked most→least similar
    assert all(0.0 < s <= 1.0 for s in scores)         # distance→similarity mapping
    assert ids[-1] == 2                                # baking is least related to "climbing"
    assert ids[0] in (1, 3)                            # a climbing entry ranks top


@pytest.mark.integration
def test_search_empty_real_collection(chroma):
    assert emb.search_similar("anything") == []


@pytest.mark.integration
def test_upsert_replaces_in_place(chroma):
    emb.embed_and_store(1, "original text about gardening")
    emb.embed_and_store(1, "updated text about astronomy and telescopes")  # same id

    results = emb.search_similar("stars and planets", limit=5)
    assert len(results) == 1        # upsert replaced, not appended
    assert results[0][0] == 1
