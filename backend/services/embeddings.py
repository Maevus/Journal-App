"""
Chroma vector store: embed entries and run similarity search.
Uses chromadb's built-in ONNX embedding function (no extra deps needed).
"""
import logging
from typing import List, Optional, Tuple

import chromadb
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

from config import CHROMA_PERSIST_DIR

logger = logging.getLogger(__name__)

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


def embed_and_store(entry_id: int, content: str, *, user_id: Optional[int]) -> None:
    """Embed one entry, stamped with its owner so the ANN query can filter by user.

    `user_id` is required but may be explicitly None (the user-less startup sync
    paths): callers must always state ownership, even to say "unowned". Chroma
    metadata can't hold NULL, so unowned vectors carry no user_id key at all —
    an absent key matches no user's filter, the vector-store spelling of SQL's
    invisible `user_id NULL` rows (ADR 0003). scripts/backfill_owner.py stamps
    both stores.
    """
    kwargs = {"documents": [content], "ids": [str(entry_id)]}
    if user_id is not None:
        kwargs["metadatas"] = [{"user_id": user_id}]
    _get_collection().upsert(**kwargs)


def search_similar(query: str, limit: int = 10, *, user_id: int) -> List[Tuple[int, float]]:
    """Return [(entry_id, similarity_score)] of `user_id`'s entries, most similar first.

    `user_id` is required and keyword-only for the same reasons as hybrid_search:
    an unscoped ANN query must be inexpressible. The `where` filter also spends
    the whole candidate pool on this user's vectors instead of ranking everyone's.
    """
    collection = _get_collection()
    count = collection.count()
    if count == 0:
        return []

    n = min(limit, count)
    results = collection.query(query_texts=[query], n_results=n, where={"user_id": user_id})

    ids = results["ids"][0]
    distances = results["distances"][0]

    # Chroma returns L2 distance; convert to a 0-1 similarity score
    return [(int(doc_id), 1.0 / (1.0 + dist)) for doc_id, dist in zip(ids, distances)]
