"""
Chroma vector store: embed entries and run similarity search.
Uses chromadb's built-in ONNX embedding function (no extra deps needed).
"""
import logging
import os
from typing import List, Tuple

import chromadb
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

logger = logging.getLogger(__name__)

CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", "./chroma_db")
COLLECTION_NAME = "journal_entries"

_collection = None


def _get_collection():
    global _collection
    if _collection is None:
        client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
        _collection = client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=DefaultEmbeddingFunction(),
        )
    return _collection


def embed_and_store(entry_id: int, content: str) -> None:
    _get_collection().upsert(
        documents=[content],
        ids=[str(entry_id)],
    )


def search_similar(query: str, limit: int = 10) -> List[Tuple[int, float]]:
    """Return [(entry_id, similarity_score)] sorted by descending similarity."""
    collection = _get_collection()
    count = collection.count()
    if count == 0:
        return []

    n = min(limit, count)
    results = collection.query(query_texts=[query], n_results=n)

    ids = results["ids"][0]
    distances = results["distances"][0]

    # Chroma returns L2 distance; convert to a 0-1 similarity score
    return [(int(doc_id), 1.0 / (1.0 + dist)) for doc_id, dist in zip(ids, distances)]
