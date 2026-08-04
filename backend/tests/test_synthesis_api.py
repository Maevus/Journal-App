"""POST /stream/synthesis — SSE streaming and the in-memory response cache.

POST, not GET: a GET was reachable by cross-site navigation with the
SameSite=Lax session cookie attached, so any page could spend a logged-in
user's budget. See the route docstring.

`stream_text` (the LLM call) is stubbed; the cache is reset per test.
"""
from collections import OrderedDict
from datetime import datetime

import routers.synthesis as synthesis
from services.quota import QuotaExceededError


def _fake_stream_factory(calls):
    async def fake_stream(*args, **kwargs):
        calls.append(1)
        for token in ["Hello", " there"]:
            yield token
    return fake_stream


async def _refusing_stream(*args, **kwargs):
    """The gate refuses at first iteration, before any token — exactly like
    the real stream_text with an exhausted budget."""
    raise QuotaExceededError("daily LLM budget exhausted")
    yield  # unreachable; makes this an async generator like the real thing


def test_get_is_not_allowed(client):
    """The CSRF fix, pinned: a cross-site page can navigate the browser into a
    GET with the SameSite=Lax session cookie attached, so the paid route must
    not answer one. POST with a JSON body forces a CORS preflight the
    FRONTEND_URL allow-list refuses."""
    assert client.get("/stream/synthesis", params={"days": 0, "limit": 5}).status_code == 405


def test_over_budget_streams_quota_frame_and_caches_nothing(client, add_entry, monkeypatch):
    """An over-budget user gets a named `event: quota` SSE frame (the refusal
    rides inside the 200 stream — the status is already sent by the time the
    gate refuses) and a clean [DONE]. The refusal must not poison the cache:
    once there's budget again, the same request must reach the LLM, not replay
    a phantom cache entry."""
    monkeypatch.setattr(synthesis, "_CACHE", OrderedDict())
    add_entry("a reflective entry", timestamp=datetime(2026, 5, 10))
    monkeypatch.setattr(synthesis, "stream_text", _refusing_stream)

    r = client.post("/stream/synthesis", json={"days": 0, "limit": 5})
    assert r.status_code == 200
    assert "event: quota" in r.text
    assert "data: [DONE]" in r.text

    # Budget restored (stream works again) → the LLM must actually be called.
    calls = []
    monkeypatch.setattr(synthesis, "stream_text", _fake_stream_factory(calls))
    r2 = client.post("/stream/synthesis", json={"days": 0, "limit": 5})
    assert len(calls) == 1
    assert "Hello there" not in r.text  # and the refused response carried no text


def test_free_tier_synthesis_runs_resolved_model(client, add_entry, monkeypatch):
    """Synthesis runs the per-user resolved model (free tier → haiku), not the
    prompt-YAML default — otherwise a free-tier user is billed at opus rates."""
    monkeypatch.setattr(synthesis, "_CACHE", OrderedDict())
    add_entry("a reflective entry", timestamp=datetime(2026, 5, 10))
    seen = {}

    async def fake_stream(*args, **kwargs):
        seen["model"] = kwargs.get("model")
        yield "ok"

    monkeypatch.setattr(synthesis, "stream_text", fake_stream)
    client.post("/stream/synthesis", json={"days": 0, "limit": 5})
    assert seen["model"] == "claude-haiku-4-5"


def test_miss_streams_tokens_then_caches(client, add_entry, monkeypatch):
    monkeypatch.setattr(synthesis, "_CACHE", OrderedDict())
    add_entry("a reflective entry", timestamp=datetime(2026, 5, 10))
    calls = []
    monkeypatch.setattr(synthesis, "stream_text", _fake_stream_factory(calls))

    r1 = client.post("/stream/synthesis", json={"days": 0, "limit": 5})
    assert r1.status_code == 200
    assert 'data: "Hello"' in r1.text
    assert "data: [DONE]" in r1.text
    assert len(calls) == 1  # one LLM call on a miss


def test_hit_replays_without_second_llm_call(client, add_entry, monkeypatch):
    monkeypatch.setattr(synthesis, "_CACHE", OrderedDict())
    add_entry("a reflective entry", timestamp=datetime(2026, 5, 10))
    calls = []
    monkeypatch.setattr(synthesis, "stream_text", _fake_stream_factory(calls))

    client.post("/stream/synthesis", json={"days": 0, "limit": 5})  # populate cache
    r2 = client.post("/stream/synthesis", json={"days": 0, "limit": 5})  # identical → hit

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

    client.post("/stream/synthesis", json={"days": 0, "limit": 5, "source": "google_keep"})
    client.post("/stream/synthesis", json={"days": 0, "limit": 5, "source": "goodnotes"})
    assert len(calls) == 2
