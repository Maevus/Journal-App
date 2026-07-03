"""Slice F1: every LLM call persists one attributed LLMUsage row.

The meter lives in llm_client._log_usage — the one place both LLM entry points
already funnel through — so an unmetered call is structurally impossible (the
spend-side twin of the user_entries read choke point). The Anthropic SDK is
faked (no API calls); meter writes land in the in-memory `meter_db` engine
from conftest.
"""
from types import SimpleNamespace

import pytest
from pydantic import BaseModel
from sqlmodel import Session, select

import services.llm_client as llm
from models.usage import LLMUsage


class _FakeStream:
    """Async context manager mimicking client.messages.stream(...)."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @property
    def text_stream(self):
        async def gen():
            for t in ("hel", "lo"):
                yield t

        return gen()

    async def get_final_message(self):
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=1000, output_tokens=500))


def _fake_streaming_client(monkeypatch):
    fake = SimpleNamespace(messages=SimpleNamespace(stream=lambda **kw: _FakeStream()))
    monkeypatch.setattr(llm, "_async_client", lambda: fake)


class _Out(BaseModel):
    x: int


def _fake_tool_client(monkeypatch):
    response = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=1000, output_tokens=500),
        content=[SimpleNamespace(type="tool_use", name="structured_extract", input={"x": 1})],
    )

    async def create(**kw):
        return response

    fake = SimpleNamespace(messages=SimpleNamespace(create=create))
    monkeypatch.setattr(llm, "_async_client", lambda: fake)


def _rows(engine):
    with Session(engine) as s:
        return s.exec(select(LLMUsage)).all()


async def test_stream_text_persists_an_attributed_usage_row(monkeypatch, meter_db):
    _fake_streaming_client(monkeypatch)

    async for _ in llm.stream_text(prompt="hi", model="claude-haiku-4-5", user_id=7):
        pass

    [row] = _rows(meter_db)
    assert row.user_id == 7
    assert row.model == "claude-haiku-4-5"
    assert (row.input_tokens, row.output_tokens) == (1000, 500)
    # haiku: $1/1M in, $5/1M out — the meter records real dollars, not tokens
    assert row.cost == pytest.approx(1000 * 1e-6 + 500 * 5e-6)


async def test_structured_output_persists_a_system_row(monkeypatch, meter_db):
    _fake_tool_client(monkeypatch)

    await llm.structured_output(prompt="p", output_schema=_Out, user_id=None)

    [row] = _rows(meter_db)
    assert row.user_id is None  # system work: visible cost, nobody's quota


def test_llm_calls_without_attribution_are_impossible():
    """user_id is required keyword-only — a caller that doesn't say who's
    billed fails at call time, not as an anonymous meter row."""
    with pytest.raises(TypeError):
        llm.stream_text(prompt="hi")


async def test_meter_failure_never_kills_the_call(monkeypatch, meter_db):
    """A failed meter INSERT after a successful stream must log, not blow up
    the SSE mid-flight. (Quota, in F2, fails closed — but it runs BEFORE the
    call, so nothing is lost by this leniency.)"""
    _fake_streaming_client(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("db locked")

    monkeypatch.setattr(llm, "Session", boom)

    tokens = [t async for t in llm.stream_text(prompt="hi", user_id=1)]

    assert tokens == ["hel", "lo"]  # the stream still completed


def test_synthesis_bills_the_logged_in_user(client, me, add_entry, monkeypatch):
    from collections import OrderedDict

    import routers.synthesis as synthesis

    monkeypatch.setattr(synthesis, "_CACHE", OrderedDict())
    add_entry("a day worth synthesising")
    seen = {}

    async def fake_stream(*args, **kwargs):
        seen["user_id"] = kwargs.get("user_id")
        yield "ok"

    monkeypatch.setattr(synthesis, "stream_text", fake_stream)

    resp = client.get("/stream/synthesis", params={"days": 0, "limit": 5})

    assert resp.status_code == 200
    assert seen["user_id"] == me.id


def test_chat_bills_the_logged_in_user(client, me, add_entry, monkeypatch):
    import routers.chat as chat

    add_entry("an entry to retrieve")
    seen = {}

    async def fake_stream(*args, **kwargs):
        seen["user_id"] = kwargs.get("user_id")
        yield "ok"

    monkeypatch.setattr(chat, "stream_text", fake_stream)

    resp = client.post(
        "/stream/chat", json={"messages": [{"role": "user", "content": "how was May?"}]}
    )

    assert resp.status_code == 200
    assert seen["user_id"] == me.id


async def test_mood_scoring_bills_the_entry_owner(monkeypatch, meter_db):
    import services.ingestion as ingestion

    monkeypatch.setattr(ingestion, "engine", meter_db)  # write-back stays in-memory
    seen = {}

    async def fake_structured(*args, **kwargs):
        seen["user_id"] = kwargs.get("user_id")
        return ingestion._MoodScore(score=0.5, reasoning="even")

    monkeypatch.setattr(llm, "structured_output", fake_structured)

    await ingestion.score_mood_background(999, "some entry text", user_id=42)

    assert seen["user_id"] == 42


async def test_retrieval_helpers_bill_the_asking_user(monkeypatch):
    """parse_date_range and condense_query spend money on behalf of the chat
    user — both must forward the attribution to structured_output."""
    import services.retrieval as retrieval

    seen = []

    async def fake_structured(*args, **kwargs):
        seen.append(kwargs.get("user_id"))
        return retrieval.DateRange(is_time_scoped=False)

    monkeypatch.setattr(retrieval, "structured_output", fake_structured)
    await retrieval.parse_date_range("energy in May", user_id=5)

    async def fake_condense(*args, **kwargs):
        seen.append(kwargs.get("user_id"))
        return retrieval.StandaloneQuery(standalone_query="q")

    monkeypatch.setattr(retrieval, "structured_output", fake_condense)
    await retrieval.condense_query([{"role": "user", "content": "earlier"}], "and June?", user_id=5)

    assert seen == [5, 5]
