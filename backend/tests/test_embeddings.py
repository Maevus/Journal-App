"""Tests for the Chroma vector-store wrapper (collection mocked — no real Chroma)."""
from unittest.mock import MagicMock

import services.embeddings as emb


def test_embed_and_store_upserts(monkeypatch):
    col = MagicMock()
    monkeypatch.setattr(emb, "_get_collection", lambda: col)
    emb.embed_and_store(5, "hello world")
    col.upsert.assert_called_once_with(documents=["hello world"], ids=["5"])


def test_search_similar_empty_collection(monkeypatch):
    col = MagicMock()
    col.count.return_value = 0
    monkeypatch.setattr(emb, "_get_collection", lambda: col)
    assert emb.search_similar("query") == []
    col.query.assert_not_called()


def test_search_similar_converts_distance_to_similarity(monkeypatch):
    col = MagicMock()
    col.count.return_value = 2
    col.query.return_value = {"ids": [["1", "2"]], "distances": [[0.0, 3.0]]}
    monkeypatch.setattr(emb, "_get_collection", lambda: col)

    result = emb.search_similar("query", limit=10)
    # similarity = 1 / (1 + distance)
    assert result == [(1, 1.0), (2, 0.25)]
    col.query.assert_called_once_with(query_texts=["query"], n_results=2)  # n = min(limit, count)


def test_get_collection_is_cached(monkeypatch):
    monkeypatch.setattr(emb, "_collection", None)
    fake_col = object()
    fake_client = MagicMock()
    fake_client.get_or_create_collection.return_value = fake_col
    monkeypatch.setattr(emb.chromadb, "PersistentClient", lambda **k: fake_client)
    monkeypatch.setattr(emb, "DefaultEmbeddingFunction", lambda: None)

    first = emb._get_collection()
    second = emb._get_collection()
    assert first is fake_col and second is fake_col
    fake_client.get_or_create_collection.assert_called_once()  # built once, then cached
