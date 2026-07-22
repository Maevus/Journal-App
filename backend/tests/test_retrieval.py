"""services.retrieval.hybrid_search — 0.7 semantic + 0.3 BM25 fusion.

`search_similar` (the Chroma call) is stubbed; BM25 runs for real over content.
"""
from datetime import datetime

import services.retrieval as retrieval
from models.journal import JournalEntry


def _add(session, content, source="text", timestamp=None, user_id=1, mood_score=None):
    entry = JournalEntry(
        user_id=user_id,
        source=source,
        content=content,
        content_hash=content,
        mood_score=mood_score,
        **({"timestamp": timestamp} if timestamp is not None else {}),
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


# --- mood_summary (whole-journal aggregate for the Ask agent) --------------

def test_mood_summary_none_when_nothing_scored(session):
    _add(session, "an unscored entry")  # mood_score defaults to None
    assert retrieval.mood_summary(session, user_id=1) is None


def test_mood_summary_aggregates_overall_and_by_year(session):
    _add(session, "a", timestamp=datetime(2025, 3, 1), mood_score=0.4)
    _add(session, "b", timestamp=datetime(2025, 9, 1), mood_score=0.6)
    _add(session, "c", timestamp=datetime(2026, 1, 1), mood_score=0.9)
    _add(session, "unscored", timestamp=datetime(2026, 2, 1))  # excluded

    s = retrieval.mood_summary(session, user_id=1)
    assert s is not None
    assert s.count == 3  # the unscored entry is not counted
    assert s.overall_avg == round((0.4 + 0.6 + 0.9) / 3, 3)
    assert s.first_date == "2025-03-01"
    assert s.last_date == "2026-01-01"
    assert s.by_year == [
        {"year": 2025, "avg": 0.5, "count": 2},
        {"year": 2026, "avg": 0.9, "count": 1},
    ]


def test_mood_summary_is_user_scoped(session):
    _add(session, "mine", user_id=1, mood_score=0.8)
    _add(session, "theirs", user_id=2, mood_score=0.1)
    s = retrieval.mood_summary(session, user_id=1)
    assert s.count == 1 and s.overall_avg == 0.8  # only user 1's entry


def test_render_mood_summary_is_a_compact_block():
    s = retrieval.MoodSummary(
        count=3, overall_avg=0.633, first_date="2025-03-01", last_date="2026-01-01",
        by_year=[{"year": 2025, "avg": 0.5, "count": 2}, {"year": 2026, "avg": 0.9, "count": 1}],
    )
    block = retrieval.render_mood_summary(s)
    assert "0.63" in block  # overall average, 2dp
    assert "3 scored entries" in block
    assert "2025-03-01 to 2026-01-01" in block
    assert "2025: 0.50 (n=2)" in block and "2026: 0.90 (n=1)" in block


def test_empty_db_returns_empty(session):
    assert retrieval.hybrid_search(session, "anything", 5, user_id=1) == []


def test_bm25_surfaces_keyword_without_semantic(session, monkeypatch):
    a = _add(session, "rock climbing adventure")
    _add(session, "quiet reading evening")
    monkeypatch.setattr(retrieval, "search_similar", lambda q, limit, *, user_id=None: [])

    hits = retrieval.hybrid_search(session, "climbing", 5, user_id=1)
    assert hits[0][0].id == a.id


def test_semantic_signal_ranks_first(session, monkeypatch):
    a = _add(session, "climbing at the gym")
    _add(session, "cooking dinner")
    # Query matches nothing textually → ranking comes purely from the stub.
    monkeypatch.setattr(retrieval, "search_similar", lambda q, limit, *, user_id=None: [(a.id, 1.0)])

    hits = retrieval.hybrid_search(session, "zzz", 5, user_id=1)
    assert hits[0][0].id == a.id


def test_respects_limit(session, monkeypatch):
    for i in range(5):
        _add(session, f"entry number {i}")
    monkeypatch.setattr(retrieval, "search_similar", lambda q, limit, *, user_id=None: [])

    hits = retrieval.hybrid_search(session, "entry", 2, user_id=1)
    assert len(hits) == 2


def test_date_window_excludes_out_of_range_entries(session, monkeypatch):
    """A start/end window must drop entries outside it — even ones the semantic
    signal ranks first. This is the date-blind-retrieval regression."""
    in_window = _add(session, "energy high on concerta today", timestamp=datetime(2026, 5, 11))
    out_window = _add(session, "energy high on concerta lately", timestamp=datetime(2025, 11, 11))
    # Semantic ranks the OUT-of-window entry top; without a date filter it'd win.
    monkeypatch.setattr(retrieval, "search_similar", lambda q, limit, *, user_id=None: [(out_window.id, 1.0)])

    hits = retrieval.hybrid_search(
        session, "energy concerta", 5,
        start=datetime(2026, 5, 1), end=datetime(2026, 6, 1), user_id=1,
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
    start, end = await retrieval.parse_date_range("energy in May and June", user_id=1)
    assert start == datetime(2026, 5, 1)
    assert end == datetime(2026, 7, 1)  # inclusive end_date + 1 day -> exclusive


async def test_parse_date_range_none_when_not_scoped(monkeypatch):
    monkeypatch.setattr(
        retrieval, "structured_output",
        _fake_structured(retrieval.DateRange(is_time_scoped=False)),
    )
    assert await retrieval.parse_date_range("what did I write about climbing", user_id=1) == (None, None)


async def test_parse_date_range_swallows_llm_errors(monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("api down")

    monkeypatch.setattr(retrieval, "structured_output", boom)
    assert await retrieval.parse_date_range("energy in May", user_id=1) == (None, None)


async def test_parse_date_range_ignores_bad_iso(monkeypatch):
    monkeypatch.setattr(
        retrieval, "structured_output",
        _fake_structured(retrieval.DateRange(
            is_time_scoped=True, start_date="May 2026", end_date="not-a-date")),
    )
    assert await retrieval.parse_date_range("energy in May", user_id=1) == (None, None)


async def test_parse_date_range_rejects_inverted_window(monkeypatch):
    # end before start would match nothing — fall back to unscoped, not an empty window.
    monkeypatch.setattr(
        retrieval, "structured_output",
        _fake_structured(retrieval.DateRange(
            is_time_scoped=True, start_date="2026-06-01", end_date="2026-05-01")),
    )
    assert await retrieval.parse_date_range("energy in June then May", user_id=1) == (None, None)


async def test_parse_date_range_rejects_time_bearing_iso(monkeypatch):
    # A datetime string (not a bare date) is rejected so it can't shift a day boundary.
    monkeypatch.setattr(
        retrieval, "structured_output",
        _fake_structured(retrieval.DateRange(
            is_time_scoped=True, start_date="2026-05-01T12:00:00", end_date="2026-05-31")),
    )
    assert await retrieval.parse_date_range("energy in May", user_id=1) == (None, None)


# --- condense_query (LLM stubbed) ---

async def test_condense_query_first_turn_skips_llm(monkeypatch):
    """No history → the question already stands alone; no LLM call is made."""
    async def boom(*args, **kwargs):
        raise AssertionError("structured_output should not be called on the first turn")

    monkeypatch.setattr(retrieval, "structured_output", boom)
    assert await retrieval.condense_query([], "how was my mood in May?", user_id=1) == "how was my mood in May?"


async def test_condense_query_rewrites_followup(monkeypatch):
    monkeypatch.setattr(
        retrieval, "structured_output",
        _fake_structured(retrieval.StandaloneQuery(standalone_query="how was my mood in June?")),
    )
    history = [
        {"role": "user", "content": "how was my mood in May?"},
        {"role": "assistant", "content": "Steady in May."},
    ]
    assert await retrieval.condense_query(history, "and June?", user_id=1) == "how was my mood in June?"


async def test_condense_query_degrades_to_raw_on_error(monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("api down")

    monkeypatch.setattr(retrieval, "structured_output", boom)
    history = [{"role": "user", "content": "earlier"}]
    assert await retrieval.condense_query(history, "and June?", user_id=1) == "and June?"


async def test_condense_query_falls_back_when_blank(monkeypatch):
    monkeypatch.setattr(
        retrieval, "structured_output",
        _fake_structured(retrieval.StandaloneQuery(standalone_query="   ")),
    )
    history = [{"role": "user", "content": "earlier"}]
    assert await retrieval.condense_query(history, "and June?", user_id=1) == "and June?"


async def test_quota_refusal_degrades_like_any_llm_failure(monkeypatch):
    """F2 pin: chat's cheap helpers must keep degrading gracefully when the
    quota gate refuses their haiku calls — the refusal costs $0 and the turn
    still gets stopped (with the user-facing quota frame) at its main call.
    Guards against ever narrowing their broad `except` past QuotaExceededError."""
    from services.quota import QuotaExceededError

    async def refuse(*args, **kwargs):
        raise QuotaExceededError("daily LLM budget exhausted")

    monkeypatch.setattr(retrieval, "structured_output", refuse)
    assert await retrieval.parse_date_range("energy in May", user_id=1) == (None, None)
    assert (
        await retrieval.condense_query(
            [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
            "and June?",
            user_id=1,
        )
        == "and June?"
    )
