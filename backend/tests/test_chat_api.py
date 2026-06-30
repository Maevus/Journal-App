"""POST /stream/chat — multi-turn RAG SSE.

`condense_query`, `parse_date_range`, `hybrid_search` (retrieval) and
`stream_text` (the LLM call) are stubbed.
"""
import json
from datetime import datetime

import routers.chat as chat
from models.journal import JournalEntry


def _fake_stream_factory(calls):
    async def fake_stream(*args, **kwargs):
        calls.append(kwargs)
        for token in ["Answer", " text [1]"]:
            yield token
    return fake_stream


def _fake_parse_factory(window=(None, None)):
    async def fake_parse(question, **kwargs):
        return window
    return fake_parse


def _fake_condense_factory(rewritten=None, seen=None):
    async def fake_condense(history, question):
        if seen is not None:
            seen.update(history=history, question=question)
        return rewritten if rewritten is not None else question
    return fake_condense


def _sources_from_response(text):
    """Pull the JSON payload of the `event: sources` SSE frame out of the body."""
    for block in text.split("\n\n"):
        if block.startswith("event: sources"):
            data_line = block.splitlines()[-1]
            return json.loads(data_line[len("data: "):])
    return None


def test_chat_streams_answer_sources_and_retrieves_for_question(client, monkeypatch):
    seen = {}

    def fake_hybrid(session, query, limit, start=None, end=None):
        seen.update(query=query, limit=limit, start=start, end=end)
        return [
            (
                JournalEntry(
                    id=42,
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
    monkeypatch.setattr(chat, "condense_query", _fake_condense_factory())
    calls = []
    monkeypatch.setattr(chat, "stream_text", _fake_stream_factory(calls))

    r = client.post(
        "/stream/chat",
        json={"messages": [{"role": "user", "content": "what did I write about climbing"}], "limit": 5},
    )

    assert r.status_code == 200
    assert 'data: "Answer"' in r.text
    assert "data: [DONE]" in r.text
    assert seen == {"query": "what did I write about climbing", "limit": 5,
                    "start": None, "end": None}
    assert len(calls) == 1  # one LLM call

    sources = _sources_from_response(r.text)
    assert sources == [{
        "n": 1, "id": 42, "date": "2026-05-10",
        "source": "text", "snippet": "went climbing at the gym",
    }]


def test_chat_condenses_followup_before_retrieval(client, monkeypatch):
    """A follow-up is rewritten to a standalone query, which then feeds retrieval
    and date parsing (not the raw fragment)."""
    seen_condense = {}
    seen_parse = {}
    seen_hybrid = {}

    async def fake_parse(question, **kwargs):
        seen_parse["question"] = question
        return (None, None)

    def fake_hybrid(session, query, limit, start=None, end=None):
        seen_hybrid["query"] = query
        return [(JournalEntry(id=1, source="text", content="c", content_hash="h",
                              timestamp=datetime(2026, 6, 1)), 0.5)]

    monkeypatch.setattr(
        chat, "condense_query",
        _fake_condense_factory(rewritten="how was my mood in June?", seen=seen_condense),
    )
    monkeypatch.setattr(chat, "parse_date_range", fake_parse)
    monkeypatch.setattr(chat, "hybrid_search", fake_hybrid)
    monkeypatch.setattr(chat, "stream_text", _fake_stream_factory([]))

    r = client.post("/stream/chat", json={"messages": [
        {"role": "user", "content": "how was my mood in May?"},
        {"role": "assistant", "content": "In May you felt steady."},
        {"role": "user", "content": "and June?"},
    ]})

    assert r.status_code == 200
    # condense_query saw the prior turns + the new question
    assert seen_condense["question"] == "and June?"
    assert len(seen_condense["history"]) == 2
    # the rewritten standalone query — not "and June?" — drove retrieval + parsing
    assert seen_parse["question"] == "how was my mood in June?"
    assert seen_hybrid["query"] == "how was my mood in June?"


def test_chat_passes_parsed_date_window_to_retrieval(client, monkeypatch):
    window = (datetime(2026, 5, 1), datetime(2026, 7, 1))
    seen = {}

    def fake_hybrid(session, query, limit, start=None, end=None):
        seen.update(start=start, end=end)
        return [(JournalEntry(id=1, source="text", content="c", content_hash="h",
                              timestamp=datetime(2026, 5, 1)), 0.5)]

    monkeypatch.setattr(chat, "hybrid_search", fake_hybrid)
    monkeypatch.setattr(chat, "parse_date_range", _fake_parse_factory(window))
    monkeypatch.setattr(chat, "condense_query", _fake_condense_factory())
    monkeypatch.setattr(chat, "stream_text", _fake_stream_factory([]))

    r = client.post("/stream/chat", json={"messages": [
        {"role": "user", "content": "energy in May and June"},
    ]})

    assert r.status_code == 200
    assert seen == {"start": window[0], "end": window[1]}


def test_chat_falls_back_to_unscoped_when_window_empty(client, monkeypatch):
    window = (datetime(2020, 1, 1), datetime(2020, 2, 1))
    calls = []

    def fake_hybrid(session, query, limit, start=None, end=None):
        calls.append((start, end))
        if start is None and end is None:
            return [(JournalEntry(id=1, source="text", content="c", content_hash="h",
                                  timestamp=datetime(2026, 5, 1)), 0.5)]
        return []

    monkeypatch.setattr(chat, "hybrid_search", fake_hybrid)
    monkeypatch.setattr(chat, "parse_date_range", _fake_parse_factory(window))
    monkeypatch.setattr(chat, "condense_query", _fake_condense_factory())
    stream_calls = []
    monkeypatch.setattr(chat, "stream_text", _fake_stream_factory(stream_calls))

    r = client.post("/stream/chat", json={"messages": [
        {"role": "user", "content": "what did I do in January 2020"},
    ]})

    assert r.status_code == 200
    assert calls == [window, (None, None)]  # windowed first, then unscoped fallback
    assert len(stream_calls) == 1  # still answers (from the unscoped retry)


def test_blank_last_message_short_circuits(client, monkeypatch):
    calls = []
    retrieved = []
    monkeypatch.setattr(chat, "stream_text", _fake_stream_factory(calls))
    monkeypatch.setattr(chat, "hybrid_search", lambda *a, **k: retrieved.append(1) or [])

    r = client.post("/stream/chat", json={"messages": [{"role": "user", "content": "   "}]})

    assert r.status_code == 200
    assert "data: [DONE]" in r.text
    assert len(calls) == 0  # no LLM call
    assert len(retrieved) == 0  # no retrieval either
