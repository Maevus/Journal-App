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
    async def fake_condense(history, question, *, user_id=None):
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


def test_chat_runs_resolved_model(client, me, monkeypatch):
    """Chat runs the per-user resolved model (free tier → haiku), not the
    prompt-YAML default — otherwise a free-tier user is billed at opus rates."""
    monkeypatch.setattr(chat, "hybrid_search", lambda *a, **k: [])
    monkeypatch.setattr(chat, "parse_date_range", _fake_parse_factory())
    monkeypatch.setattr(chat, "condense_query", _fake_condense_factory())
    calls = []
    monkeypatch.setattr(chat, "stream_text", _fake_stream_factory(calls))

    client.post(
        "/stream/chat",
        json={"messages": [{"role": "user", "content": "anything"}], "limit": 5},
    )
    assert calls[0]["model"] == "claude-haiku-4-5"


def test_chat_streams_answer_sources_and_retrieves_for_question(client, me, monkeypatch):
    seen = {}

    def fake_hybrid(session, query, limit, start=None, end=None, *, user_id=None):
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


def test_chat_condenses_followup_before_retrieval(client, me, monkeypatch):
    """A follow-up is rewritten to a standalone query, which then feeds retrieval
    and date parsing (not the raw fragment)."""
    seen_condense = {}
    seen_parse = {}
    seen_hybrid = {}

    async def fake_parse(question, **kwargs):
        seen_parse["question"] = question
        return (None, None)

    def fake_hybrid(session, query, limit, start=None, end=None, *, user_id=None):
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


def test_chat_sends_prior_turns_and_raw_question_to_llm(client, me, monkeypatch):
    """Prior turns pass through as real messages; the final user turn carries
    retrieved entries + the *raw* question (retrieval used the condensed one)."""
    def fake_hybrid(session, query, limit, start=None, end=None, *, user_id=None):
        return [(JournalEntry(id=1, source="text", content="CONTENT_TOKEN",
                              content_hash="h", timestamp=datetime(2026, 6, 1)), 0.5)]

    monkeypatch.setattr(chat, "hybrid_search", fake_hybrid)
    monkeypatch.setattr(chat, "parse_date_range", _fake_parse_factory())
    monkeypatch.setattr(chat, "condense_query", _fake_condense_factory(rewritten="STANDALONE_QUERY"))
    calls = []
    monkeypatch.setattr(chat, "stream_text", _fake_stream_factory(calls))

    r = client.post("/stream/chat", json={"messages": [
        {"role": "user", "content": "how was my mood in May?"},
        {"role": "assistant", "content": "Steady in May."},
        {"role": "user", "content": "and June?"},
    ]})

    assert r.status_code == 200
    sent = calls[0]["messages"]
    # Prior turns passed through verbatim …
    assert sent[0] == {"role": "user", "content": "how was my mood in May?"}
    assert sent[1] == {"role": "assistant", "content": "Steady in May."}
    # … then a final user turn carrying the entries + the raw question.
    assert sent[-1]["role"] == "user"
    assert "CONTENT_TOKEN" in sent[-1]["content"]
    assert "and June?" in sent[-1]["content"]  # raw question, not the standalone
    assert "STANDALONE_QUERY" not in sent[-1]["content"]
    assert calls[0]["system"]  # system prompt is passed


def test_chat_filters_empty_history_messages(client, me, monkeypatch):
    """An empty-content prior turn (e.g. an aborted answer) is dropped before
    building the LLM messages — the API rejects empty non-final content."""
    monkeypatch.setattr(
        chat, "hybrid_search",
        lambda *a, **k: [(JournalEntry(id=1, source="text", content="c",
                                       content_hash="h", timestamp=datetime(2026, 5, 1)), 0.5)],
    )
    monkeypatch.setattr(chat, "parse_date_range", _fake_parse_factory())
    monkeypatch.setattr(chat, "condense_query", _fake_condense_factory())
    calls = []
    monkeypatch.setattr(chat, "stream_text", _fake_stream_factory(calls))

    r = client.post("/stream/chat", json={"messages": [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "   "},  # empty answer (whitespace)
        {"role": "user", "content": "second question"},
    ]})

    assert r.status_code == 200
    sent = calls[0]["messages"]
    assert all(m["content"].strip() for m in sent)  # no empty-content message survives
    assert sent[0] == {"role": "user", "content": "first question"}
    assert sent[-1]["role"] == "user" and "second question" in sent[-1]["content"]


def test_chat_passes_parsed_date_window_to_retrieval(client, me, monkeypatch):
    window = (datetime(2026, 5, 1), datetime(2026, 7, 1))
    seen = {}

    def fake_hybrid(session, query, limit, start=None, end=None, *, user_id=None):
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


def test_chat_falls_back_to_unscoped_when_window_empty(client, me, monkeypatch):
    window = (datetime(2020, 1, 1), datetime(2020, 2, 1))
    calls = []

    def fake_hybrid(session, query, limit, start=None, end=None, *, user_id=None):
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


def test_blank_last_message_short_circuits(client, me, monkeypatch):
    calls = []
    retrieved = []
    monkeypatch.setattr(chat, "stream_text", _fake_stream_factory(calls))
    monkeypatch.setattr(chat, "hybrid_search", lambda *a, **k: retrieved.append(1) or [])

    r = client.post("/stream/chat", json={"messages": [{"role": "user", "content": "   "}]})

    assert r.status_code == 200
    assert "data: [DONE]" in r.text
    assert len(calls) == 0  # no LLM call
    assert len(retrieved) == 0  # no retrieval either


async def _refusing_stream(*args, **kwargs):
    """The F2 gate refuses at first iteration, before any token."""
    from services.quota import QuotaExceededError

    raise QuotaExceededError("daily LLM budget exhausted")
    yield  # unreachable; makes this an async generator like the real thing


def test_over_budget_streams_quota_frame_without_sources(client, me, monkeypatch):
    """Over-budget gets `event: quota` + a clean [DONE], and no `event: sources`
    frame — citation chips for an answer that never happened would be orphans."""
    monkeypatch.setattr(chat, "hybrid_search", lambda *a, **k: [])
    monkeypatch.setattr(chat, "parse_date_range", _fake_parse_factory())
    monkeypatch.setattr(chat, "condense_query", _fake_condense_factory())
    monkeypatch.setattr(chat, "stream_text", _refusing_stream)

    r = client.post(
        "/stream/chat",
        json={"messages": [{"role": "user", "content": "how was May?"}], "limit": 5},
    )

    assert r.status_code == 200
    assert "event: quota" in r.text
    assert "data: [DONE]" in r.text
    assert "event: sources" not in r.text


def test_chat_gives_agent_per_entry_mood_and_whole_journal_summary(
    client, me, add_entry, monkeypatch
):
    """The prompt carries each retrieved entry's mood plus a whole-journal mood
    summary, so it can answer both "how did I feel then" and "what's my trend"."""
    # Scored entries the summary aggregates over; the retrieved hit below is a
    # separate stub not in the DB, so it isn't double-counted.
    add_entry("older", mood_score=0.4, timestamp=datetime(2025, 6, 1))
    add_entry("newer", mood_score=0.6, timestamp=datetime(2026, 6, 1))

    def fake_hybrid(session, query, limit, start=None, end=None, *, user_id=None):
        return [(JournalEntry(id=9, source="text", content="a climbing day",
                              content_hash="h", timestamp=datetime(2026, 5, 10),
                              mood_score=0.7), 0.9)]

    monkeypatch.setattr(chat, "hybrid_search", fake_hybrid)
    monkeypatch.setattr(chat, "parse_date_range", _fake_parse_factory())
    monkeypatch.setattr(chat, "condense_query", _fake_condense_factory())
    calls = []
    monkeypatch.setattr(chat, "stream_text", _fake_stream_factory(calls))

    client.post(
        "/stream/chat",
        json={"messages": [{"role": "user", "content": "what's my average mood?"}], "limit": 5},
    )

    prompt = calls[0]["messages"][-1]["content"]
    assert "mood: 0.70" in prompt  # per-entry score (option 1)
    assert "Mood analytics" in prompt  # summary block present (option 2)
    assert "average 0.50 across 2 scored entries" in prompt
    assert "2025: 0.40 (n=1)" in prompt and "2026: 0.60 (n=1)" in prompt


def test_chat_omits_mood_summary_when_nothing_scored(client, me, monkeypatch):
    """No scored entries → no analytics block (mood_summary returns None), but
    per-entry mood still renders as 'unscored'."""
    monkeypatch.setattr(
        chat, "hybrid_search",
        lambda *a, **k: [(JournalEntry(id=1, source="text", content="c",
                                       content_hash="h", timestamp=datetime(2026, 5, 1)), 0.5)],
    )
    monkeypatch.setattr(chat, "parse_date_range", _fake_parse_factory())
    monkeypatch.setattr(chat, "condense_query", _fake_condense_factory())
    calls = []
    monkeypatch.setattr(chat, "stream_text", _fake_stream_factory(calls))

    client.post("/stream/chat", json={"messages": [{"role": "user", "content": "hi"}]})

    prompt = calls[0]["messages"][-1]["content"]
    assert "Mood analytics" not in prompt
    assert "mood: unscored" in prompt
