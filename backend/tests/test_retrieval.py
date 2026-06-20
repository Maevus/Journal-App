"""services.retrieval.hybrid_search — 0.7 semantic + 0.3 BM25 fusion.

`search_similar` (the Chroma call) is stubbed; BM25 runs for real over content.
"""
from datetime import datetime

import services.retrieval as retrieval
from models.journal import JournalEntry


def _add(session, content, source="text", timestamp=None):
    entry = JournalEntry(
        source=source,
        content=content,
        content_hash=content,
        **({"timestamp": timestamp} if timestamp is not None else {}),
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def test_empty_db_returns_empty(session):
    assert retrieval.hybrid_search(session, "anything", 5) == []


def test_bm25_surfaces_keyword_without_semantic(session, monkeypatch):
    a = _add(session, "rock climbing adventure")
    _add(session, "quiet reading evening")
    monkeypatch.setattr(retrieval, "search_similar", lambda q, limit: [])

    hits = retrieval.hybrid_search(session, "climbing", 5)
    assert hits[0][0].id == a.id


def test_semantic_signal_ranks_first(session, monkeypatch):
    a = _add(session, "climbing at the gym")
    _add(session, "cooking dinner")
    # Query matches nothing textually → ranking comes purely from the stub.
    monkeypatch.setattr(retrieval, "search_similar", lambda q, limit: [(a.id, 1.0)])

    hits = retrieval.hybrid_search(session, "zzz", 5)
    assert hits[0][0].id == a.id


def test_respects_limit(session, monkeypatch):
    for i in range(5):
        _add(session, f"entry number {i}")
    monkeypatch.setattr(retrieval, "search_similar", lambda q, limit: [])

    hits = retrieval.hybrid_search(session, "entry", 2)
    assert len(hits) == 2


def test_date_window_excludes_out_of_range_entries(session, monkeypatch):
    """A start/end window must drop entries outside it — even ones the semantic
    signal ranks first. This is the date-blind-retrieval regression."""
    in_window = _add(session, "energy high on concerta today", timestamp=datetime(2026, 5, 11))
    out_window = _add(session, "energy high on concerta lately", timestamp=datetime(2025, 11, 11))
    # Semantic ranks the OUT-of-window entry top; without a date filter it'd win.
    monkeypatch.setattr(retrieval, "search_similar", lambda q, limit: [(out_window.id, 1.0)])

    hits = retrieval.hybrid_search(
        session, "energy concerta", 5,
        start=datetime(2026, 5, 1), end=datetime(2026, 6, 1),
    )
    ids = [entry.id for entry, _ in hits]
    assert in_window.id in ids
    assert out_window.id not in ids


# --- parse_date_range (LLM stubbed) ---

def _fake_structured(result):
    async def _call(*args, **kwargs):
        return result
    return _call


async def test_parse_date_range_builds_exclusive_window(monkeypatch):
    monkeypatch.setattr(
        retrieval, "structured_output",
        _fake_structured(retrieval.DateRange(
            is_time_scoped=True, start_date="2026-05-01", end_date="2026-06-30")),
    )
    start, end = await retrieval.parse_date_range("energy in May and June")
    assert start == datetime(2026, 5, 1)
    assert end == datetime(2026, 7, 1)  # inclusive end_date + 1 day -> exclusive


async def test_parse_date_range_none_when_not_scoped(monkeypatch):
    monkeypatch.setattr(
        retrieval, "structured_output",
        _fake_structured(retrieval.DateRange(is_time_scoped=False)),
    )
    assert await retrieval.parse_date_range("what did I write about climbing") == (None, None)


async def test_parse_date_range_swallows_llm_errors(monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("api down")

    monkeypatch.setattr(retrieval, "structured_output", boom)
    assert await retrieval.parse_date_range("energy in May") == (None, None)


async def test_parse_date_range_ignores_bad_iso(monkeypatch):
    monkeypatch.setattr(
        retrieval, "structured_output",
        _fake_structured(retrieval.DateRange(
            is_time_scoped=True, start_date="May 2026", end_date="not-a-date")),
    )
    assert await retrieval.parse_date_range("energy in May") == (None, None)


async def test_parse_date_range_rejects_inverted_window(monkeypatch):
    # end before start would match nothing — fall back to unscoped, not an empty window.
    monkeypatch.setattr(
        retrieval, "structured_output",
        _fake_structured(retrieval.DateRange(
            is_time_scoped=True, start_date="2026-06-01", end_date="2026-05-01")),
    )
    assert await retrieval.parse_date_range("energy in June then May") == (None, None)


async def test_parse_date_range_rejects_time_bearing_iso(monkeypatch):
    # A datetime string (not a bare date) is rejected so it can't shift a day boundary.
    monkeypatch.setattr(
        retrieval, "structured_output",
        _fake_structured(retrieval.DateRange(
            is_time_scoped=True, start_date="2026-05-01T12:00:00", end_date="2026-05-31")),
    )
    assert await retrieval.parse_date_range("energy in May") == (None, None)
