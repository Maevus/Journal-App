"""POST /search — hybrid 70% semantic (pgvector) + 30% BM25 fusion.

`search_similar` is stubbed so the semantic signal is controlled; BM25 runs for
real over entry content. Fusion lives in `services.retrieval`, so the stub is
applied there.
"""
import services.retrieval as retrieval

RESULT_KEYS = {"id", "content", "source", "timestamp", "similarity_score", "external_id"}


def test_empty_db_returns_no_results(client, me):
    # Short-circuits before touching the vector store.
    assert client.post("/search", json={"query": "anything", "limit": 5}).json() == {"results": []}


def test_semantic_signal_ranks_first(client, add_entry, monkeypatch):
    a = add_entry("climbing at the gym")
    add_entry("cooking dinner")
    # Query matches nothing textually, so ranking comes purely from the stub.
    monkeypatch.setattr(retrieval, "search_similar", lambda q, limit, *, user_id=None: [(a.id, 1.0)])

    results = client.post("/search", json={"query": "zzz", "limit": 5}).json()["results"]
    assert results[0]["id"] == a.id
    assert RESULT_KEYS <= results[0].keys()


def test_bm25_keyword_match_surfaces_without_semantic(client, add_entry, monkeypatch):
    a = add_entry("rock climbing adventure")
    add_entry("quiet reading evening")
    monkeypatch.setattr(retrieval, "search_similar", lambda q, limit, *, user_id=None: [])  # no semantic signal

    results = client.post("/search", json={"query": "climbing", "limit": 5}).json()["results"]
    assert results[0]["id"] == a.id  # BM25 alone surfaces the keyword match


def test_respects_limit(client, add_entry, monkeypatch):
    for i in range(5):
        add_entry(f"entry number {i}")
    monkeypatch.setattr(retrieval, "search_similar", lambda q, limit, *, user_id=None: [])
    results = client.post("/search", json={"query": "entry", "limit": 2}).json()["results"]
    assert len(results) == 2
