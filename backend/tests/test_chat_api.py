"""GET /stream/chat — single-turn RAG SSE.

`hybrid_search` (retrieval) and `stream_text` (the LLM call) are stubbed.
"""
from datetime import datetime

import routers.chat as chat
from models.journal import JournalEntry


def _fake_stream_factory(calls):
    async def fake_stream(*args, **kwargs):
        calls.append(kwargs)
        for token in ["Answer", " text"]:
            yield token
    return fake_stream


def _fake_parse_factory(window=(None, None)):
    async def fake_parse(question, **kwargs):
        return window
    return fake_parse


def test_chat_streams_answer_and_retrieves_for_question(client, monkeypatch):
    seen = {}

    def fake_hybrid(session, query, limit, start=None, end=None):
        seen.update(query=query, limit=limit, start=start, end=end)
        return [
            (
                JournalEntry(
                    source="text",
                    content="went climbing at the gym",
                    content_hash="h",
                    timestamp=datetime(2026, 5, 10),
                ),
                0.9,
            )
        ]

    monkeypatch.setattr(chat, "hybrid_search", fake_hybrid)
    monkeypatch.setattr(chat, "parse_date_range", _fake_parse_factory())
    calls = []
    monkeypatch.setattr(chat, "stream_text", _fake_stream_factory(calls))

    r = client.get("/stream/chat", params={"q": "what did I write about climbing", "limit": 5})

    assert r.status_code == 200
    assert 'data: "Answer"' in r.text
    assert "data: [DONE]" in r.text
    assert seen == {"query": "what did I write about climbing", "limit": 5,
                    "start": None, "end": None}
    assert len(calls) == 1  # one LLM call


def test_chat_passes_parsed_date_window_to_retrieval(client, monkeypatch):
    """A time-scoped question's parsed window reaches hybrid_search."""
    window = (datetime(2026, 5, 1), datetime(2026, 7, 1))
    seen = {}

    def fake_hybrid(session, query, limit, start=None, end=None):
        seen.update(start=start, end=end)
        # Return a hit so the empty-window fallback doesn't fire and overwrite `seen`.
        return [(JournalEntry(source="text", content="c", content_hash="h",
                              timestamp=datetime(2026, 5, 1)), 0.5)]

    monkeypatch.setattr(chat, "hybrid_search", fake_hybrid)
    monkeypatch.setattr(chat, "parse_date_range", _fake_parse_factory(window))
    monkeypatch.setattr(chat, "stream_text", _fake_stream_factory([]))

    r = client.get("/stream/chat", params={"q": "energy in May and June"})

    assert r.status_code == 200
    assert seen == {"start": window[0], "end": window[1]}


def test_chat_falls_back_to_unscoped_when_window_empty(client, monkeypatch):
    """A parsed window that matches nothing retries unscoped instead of claiming
    the journal is empty."""
    window = (datetime(2020, 1, 1), datetime(2020, 2, 1))
    calls = []

    def fake_hybrid(session, query, limit, start=None, end=None):
        calls.append((start, end))
        # First (windowed) call finds nothing; the unscoped retry finds an entry.
        if start is None and end is None:
            return [(JournalEntry(source="text", content="c", content_hash="h",
                                  timestamp=datetime(2026, 5, 1)), 0.5)]
        return []

    monkeypatch.setattr(chat, "hybrid_search", fake_hybrid)
    monkeypatch.setattr(chat, "parse_date_range", _fake_parse_factory(window))
    stream_calls = []
    monkeypatch.setattr(chat, "stream_text", _fake_stream_factory(stream_calls))

    r = client.get("/stream/chat", params={"q": "what did I do in January 2020"})

    assert r.status_code == 200
    assert calls == [window, (None, None)]  # windowed first, then unscoped fallback
    assert len(stream_calls) == 1  # still answers (from the unscoped retry)


def test_blank_q_short_circuits(client, monkeypatch):
    calls = []
    retrieved = []
    monkeypatch.setattr(chat, "stream_text", _fake_stream_factory(calls))
    monkeypatch.setattr(chat, "hybrid_search", lambda *a, **k: retrieved.append(1) or [])

    r = client.get("/stream/chat", params={"q": "   "})

    assert r.status_code == 200
    assert "data: [DONE]" in r.text
    assert len(calls) == 0  # no LLM call
    assert len(retrieved) == 0  # no retrieval either
