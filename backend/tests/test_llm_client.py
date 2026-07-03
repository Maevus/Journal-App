"""Tests for the Anthropic wrapper (the SDK client is faked — no real API calls)."""
import asyncio
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from pydantic import BaseModel

import services.llm_client as llm


# --- fakes for the AsyncAnthropic client -----------------------------------

class _FakeTextStream:
    """Async context manager mimicking client.messages.stream(...)."""

    def __init__(self, tokens):
        self._tokens = tokens

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @property
    def text_stream(self):
        tokens = self._tokens

        async def gen():
            for t in tokens:
                yield t

        return gen()

    async def get_final_message(self):
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=10, output_tokens=5))


class _FakeMessages:
    def __init__(self, *, stream_cm=None, stream_side_effect=None, create_side_effect=None):
        self._stream_cm = stream_cm
        self._stream_side_effect = stream_side_effect
        self._create_side_effect = create_side_effect
        self.stream_calls = 0
        self.create_calls = 0

    def stream(self, **kwargs):
        self.stream_calls += 1
        if self._stream_side_effect is not None:
            return self._stream_side_effect.pop(0)()  # callable: returns cm or raises
        return self._stream_cm

    async def create(self, **kwargs):
        self.create_calls += 1
        item = self._create_side_effect.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _client(messages):
    return SimpleNamespace(messages=messages)


async def _no_sleep(*a, **k):
    pass


def _conn_error():
    raise anthropic.APIConnectionError(request=httpx.Request("POST", "http://test"))


# --- _log_usage / _async_client --------------------------------------------

def test_log_usage_prints_cost(capsys):
    llm._log_usage("claude-opus-4-8", 1000, 100, user_id=1)
    out = capsys.readouterr().out
    assert "input_tokens=1000" in out and "cost=$" in out


def test_log_usage_prices_per_model(capsys):
    """A cheaper model logs a lower cost than opus for identical token counts."""
    llm._log_usage("claude-opus-4-8", 1000, 1000, user_id=1)
    opus = capsys.readouterr().out
    llm._log_usage("claude-haiku-4-5", 1000, 1000, user_id=1)
    haiku = capsys.readouterr().out

    def _cost(line):
        return float(line.split("cost=$")[1].split()[0])

    assert _cost(haiku) < _cost(opus)  # haiku must not be billed at opus rates


def test_log_usage_unknown_model_falls_back_to_default(capsys):
    llm._log_usage("some-future-model", 1000, 1000, user_id=1)
    unknown = capsys.readouterr().out
    llm._log_usage("claude-opus-4-8", 1000, 1000, user_id=1)
    opus = capsys.readouterr().out
    assert unknown.split("cost=$")[1] == opus.split("cost=$")[1]


def test_async_client_uses_env_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
    captured = {}
    monkeypatch.setattr(
        llm.anthropic, "AsyncAnthropic", lambda api_key=None: captured.update(key=api_key)
    )
    llm._async_client()
    assert captured["key"] == "sk-test-123"


# --- stream_text -----------------------------------------------------------

async def test_stream_text_yields_tokens(monkeypatch):
    msgs = _FakeMessages(stream_cm=_FakeTextStream(["Hello", " world"]))
    monkeypatch.setattr(llm, "_async_client", lambda: _client(msgs))
    out = [tok async for tok in llm.stream_text("prompt", user_id=1)]
    assert out == ["Hello", " world"]
    assert msgs.stream_calls == 1


async def test_stream_text_retries_on_connection_error(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    msgs = _FakeMessages(stream_side_effect=[_conn_error, lambda: _FakeTextStream(["ok"])])
    monkeypatch.setattr(llm, "_async_client", lambda: _client(msgs))
    out = [tok async for tok in llm.stream_text("prompt", user_id=1)]
    assert out == ["ok"]
    assert msgs.stream_calls == 2  # retried once


# --- structured_output -----------------------------------------------------

class _Schema(BaseModel):
    score: float
    reasoning: str


def _resp(*blocks):
    return SimpleNamespace(content=list(blocks), usage=SimpleNamespace(input_tokens=1, output_tokens=1))


async def test_structured_output_extracts_tool_use(monkeypatch):
    block = SimpleNamespace(type="tool_use", name="structured_extract", input={"score": 0.8, "reasoning": "x"})
    msgs = _FakeMessages(create_side_effect=[_resp(block)])
    monkeypatch.setattr(llm, "_async_client", lambda: _client(msgs))
    result = await llm.structured_output("prompt", _Schema, user_id=1)
    assert result.score == 0.8 and result.reasoning == "x"


async def test_structured_output_raises_without_tool_use(monkeypatch):
    msgs = _FakeMessages(create_side_effect=[_resp(SimpleNamespace(type="text"))])
    monkeypatch.setattr(llm, "_async_client", lambda: _client(msgs))
    with pytest.raises(ValueError):
        await llm.structured_output("prompt", _Schema, user_id=1)


async def test_structured_output_retries(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    block = SimpleNamespace(type="tool_use", name="structured_extract", input={"score": 0.5, "reasoning": "y"})
    msgs = _FakeMessages(
        create_side_effect=[anthropic.APIConnectionError(request=httpx.Request("POST", "http://t")), _resp(block)]
    )
    monkeypatch.setattr(llm, "_async_client", lambda: _client(msgs))
    result = await llm.structured_output("prompt", _Schema, user_id=1)
    assert result.score == 0.5
    assert msgs.create_calls == 2


async def test_stream_text_raises_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    msgs = _FakeMessages(stream_side_effect=[_conn_error, _conn_error, _conn_error])
    monkeypatch.setattr(llm, "_async_client", lambda: _client(msgs))
    with pytest.raises(anthropic.APIConnectionError):
        [tok async for tok in llm.stream_text("prompt", user_id=1)]
    assert msgs.stream_calls == 3


async def test_structured_output_raises_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    def err():
        return anthropic.APIConnectionError(request=httpx.Request("POST", "http://t"))

    msgs = _FakeMessages(create_side_effect=[err(), err(), err()])
    monkeypatch.setattr(llm, "_async_client", lambda: _client(msgs))
    with pytest.raises(anthropic.APIConnectionError):
        await llm.structured_output("prompt", _Schema, user_id=1)
    assert msgs.create_calls == 3
