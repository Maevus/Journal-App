"""GET /stream/synthesis — SSE streaming and the in-memory response cache.

`stream_text` (the LLM call) is stubbed; the cache is reset per test.
"""
from collections import OrderedDict
from datetime import datetime

import routers.synthesis as synthesis


def _fake_stream_factory(calls):
    async def fake_stream(*args, **kwargs):
        calls.append(1)
        for token in ["Hello", " there"]:
            yield token
    return fake_stream


def test_miss_streams_tokens_then_caches(client, add_entry, monkeypatch):
    monkeypatch.setattr(synthesis, "_CACHE", OrderedDict())
    add_entry("a reflective entry", timestamp=datetime(2026, 5, 10))
    calls = []
    monkeypatch.setattr(synthesis, "stream_text", _fake_stream_factory(calls))

    r1 = client.get("/stream/synthesis", params={"days": 0, "limit": 5})
    assert r1.status_code == 200
    assert 'data: "Hello"' in r1.text
    assert "data: [DONE]" in r1.text
    assert len(calls) == 1  # one LLM call on a miss


def test_hit_replays_without_second_llm_call(client, add_entry, monkeypatch):
    monkeypatch.setattr(synthesis, "_CACHE", OrderedDict())
    add_entry("a reflective entry", timestamp=datetime(2026, 5, 10))
    calls = []
    monkeypatch.setattr(synthesis, "stream_text", _fake_stream_factory(calls))

    client.get("/stream/synthesis", params={"days": 0, "limit": 5})  # populate cache
    r2 = client.get("/stream/synthesis", params={"days": 0, "limit": 5})  # identical → hit

    assert "Hello there" in r2.text  # full text replayed from cache
    assert len(calls) == 1  # no second LLM call


def test_different_scope_misses_separately(client, add_entry, monkeypatch):
    # A different source selects different entries → different rendered prompt →
    # different cache key → a separate LLM call (not a hit).
    monkeypatch.setattr(synthesis, "_CACHE", OrderedDict())
    add_entry("keep reflection", source="google_keep", timestamp=datetime(2026, 5, 10))
    add_entry("goodnotes reflection", source="goodnotes", timestamp=datetime(2026, 5, 10))
    calls = []
    monkeypatch.setattr(synthesis, "stream_text", _fake_stream_factory(calls))

    client.get("/stream/synthesis", params={"days": 0, "limit": 5, "source": "google_keep"})
    client.get("/stream/synthesis", params={"days": 0, "limit": 5, "source": "goodnotes"})
    assert len(calls) == 2
