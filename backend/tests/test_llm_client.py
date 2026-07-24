"""Tests for the Anthropic wrapper (the SDK client is faked — no real API calls)."""
import asyncio
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import BaseModel
from sqlmodel import Session

import config
import services.key_store as key_store
import services.llm_client as llm
from models.keys import UserApiKey
from services.quota import QuotaExceededError


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
        self.last_model = None  # the model string the API actually received

    def stream(self, **kwargs):
        self.stream_calls += 1
        self.last_model = kwargs.get("model")
        if self._stream_side_effect is not None:
            return self._stream_side_effect.pop(0)()  # callable: returns cm or raises
        return self._stream_cm

    async def create(self, **kwargs):
        self.create_calls += 1
        self.last_model = kwargs.get("model")
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
    llm._async_client()  # no key passed → the app env key
    assert captured["key"] == "sk-test-123"


def test_async_client_prefers_a_passed_key(monkeypatch):
    """A resolved BYO key overrides the app env key when building the client."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-app")
    captured = {}
    monkeypatch.setattr(
        llm.anthropic, "AsyncAnthropic", lambda api_key=None: captured.update(key=api_key)
    )
    llm._async_client("sk-user-own")
    assert captured["key"] == "sk-user-own"


# --- key_is_valid (F4: validate a BYO key against Anthropic on save) --------

async def test_key_is_valid_true_when_the_key_authenticates(monkeypatch):
    """A models.list() call authenticates the key without spending on
    generation — success means the key works."""
    async def ok_list():
        return SimpleNamespace(data=[])

    fake = SimpleNamespace(models=SimpleNamespace(list=ok_list))
    seen = {}

    def build(api_key=None):
        seen["api_key"] = api_key
        return fake

    monkeypatch.setattr(llm, "_async_client", build)
    assert await llm.key_is_valid("sk-ant-good") is True
    assert seen["api_key"] == "sk-ant-good"  # validated on the user's key, not the app's


async def test_key_is_valid_false_on_auth_error(monkeypatch):
    async def bad_list():
        raise anthropic.AuthenticationError(
            "invalid key",
            response=httpx.Response(401, request=httpx.Request("GET", "http://t")),
            body=None,
        )

    fake = SimpleNamespace(models=SimpleNamespace(list=bad_list))
    monkeypatch.setattr(llm, "_async_client", lambda *a, **k: fake)
    assert await llm.key_is_valid("sk-ant-bad") is False


# --- resolve_api_key (F4: BYO keys route calls to the user's own key) -------

def test_resolve_api_key_returns_the_users_own_key(monkeypatch, meter_db):
    monkeypatch.setattr(config, "APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with Session(meter_db) as s:
        key_store.store_user_key(s, user_id=1, raw_key="sk-ant-user-own")
    assert llm.resolve_api_key(user_id=1) == "sk-ant-user-own"


def test_resolve_api_key_is_none_without_a_stored_key(meter_db):
    """A free-tier user has no key → None, and _async_client falls back to the
    app key. No env read here, so the app-key fallback stays in one place."""
    assert llm.resolve_api_key(user_id=1) is None


async def test_stream_text_runs_on_the_users_own_key(monkeypatch, meter_db):
    """End to end: a user with a stored key has their call built on that key."""
    monkeypatch.setattr(config, "APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with Session(meter_db) as s:
        key_store.store_user_key(s, user_id=1, raw_key="sk-ant-user-own")

    seen = {}

    def spy_client(api_key=None):
        seen["api_key"] = api_key
        return _client(_FakeMessages(stream_cm=_FakeTextStream(["hi"])))

    monkeypatch.setattr(llm, "_async_client", spy_client)
    [tok async for tok in llm.stream_text("prompt", user_id=1)]
    assert seen["api_key"] == "sk-ant-user-own"


# --- resolve_model (F3: per-user model resolution) -------------------------

def test_resolve_model_free_tier_is_haiku():
    """A free-tier user resolves to the cheap model, not opus. This is the
    string that must later reach both the API call and _log_usage."""
    assert llm.resolve_model(user_id=1) == "claude-haiku-4-5"


def test_resolve_model_demo_user_is_premium(meter_db):
    """The demo user runs the showcase on the premium model, bounded by the demo
    budget — the whole point of the separate DEMO_MODEL knob. Per-user now: it's
    the demo *account*, not a global mode, that picks the model."""
    from models.user import User
    from services.auth import DEMO_EMAIL, DEMO_GOOGLE_SUB

    with Session(meter_db) as s:
        demo = User(google_sub=DEMO_GOOGLE_SUB, email=DEMO_EMAIL)
        s.add(demo)
        s.commit()
        s.refresh(demo)
        demo_id = demo.id

    assert llm.resolve_model(user_id=demo_id) == "claude-opus-4-8"


def test_resolve_model_byo_user_gets_premium(meter_db):
    """A user on their own key pays their own way, so they resolve to the
    premium model — not the free-tier Haiku (F4, first-class account)."""
    with Session(meter_db) as s:
        s.add(UserApiKey(user_id=1, provider="anthropic", encrypted_key=b"x"))
        s.commit()
    assert llm.resolve_model(user_id=1) == "claude-opus-4-8"


def test_resolve_model_refuses_unowned_system_work():
    """No owner, no generation. Mood scoring for unowned rows must not spend on
    the app key — resolution enforces it rather than trusting a convention."""
    with pytest.raises(ValueError):
        llm.resolve_model(user_id=None)


async def test_stream_text_resolves_model_when_unspecified(monkeypatch, capsys):
    """No explicit model → the entry point resolves per-user, and that same
    string reaches both the API call and the meter (the F3 billing invariant)."""
    msgs = _FakeMessages(stream_cm=_FakeTextStream(["hi"]))
    monkeypatch.setattr(llm, "_async_client", lambda *a, **k: _client(msgs))
    [tok async for tok in llm.stream_text("prompt", user_id=1)]
    assert msgs.last_model == "claude-haiku-4-5"  # API got the resolved model
    assert "model=claude-haiku-4-5" in capsys.readouterr().out  # meter did too


async def test_structured_output_resolves_model_when_unspecified(monkeypatch, capsys):
    """Mood scoring passes no model → resolves per-user; the resolved string
    reaches the API and the meter."""
    block = SimpleNamespace(type="tool_use", name="structured_extract", input={"score": 0.5, "reasoning": "y"})
    msgs = _FakeMessages(create_side_effect=[_resp(block)])
    monkeypatch.setattr(llm, "_async_client", lambda *a, **k: _client(msgs))
    await llm.structured_output("prompt", _Schema, user_id=1)
    assert msgs.last_model == "claude-haiku-4-5"
    assert "model=claude-haiku-4-5" in capsys.readouterr().out


async def test_stream_text_honours_explicit_model(monkeypatch):
    """Task-fixed callers (OCR, date-parse, condense) pass their own model and
    it is honored as-is — resolution never overrides an explicit choice."""
    msgs = _FakeMessages(stream_cm=_FakeTextStream(["hi"]))
    monkeypatch.setattr(llm, "_async_client", lambda *a, **k: _client(msgs))
    [tok async for tok in llm.stream_text("prompt", model="claude-sonnet-4-6", user_id=1)]
    assert msgs.last_model == "claude-sonnet-4-6"


# --- stream_text -----------------------------------------------------------

async def test_stream_text_yields_tokens(monkeypatch):
    msgs = _FakeMessages(stream_cm=_FakeTextStream(["Hello", " world"]))
    monkeypatch.setattr(llm, "_async_client", lambda *a, **k: _client(msgs))
    out = [tok async for tok in llm.stream_text("prompt", user_id=1)]
    assert out == ["Hello", " world"]
    assert msgs.stream_calls == 1


async def test_stream_text_retries_on_connection_error(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    msgs = _FakeMessages(stream_side_effect=[_conn_error, lambda: _FakeTextStream(["ok"])])
    monkeypatch.setattr(llm, "_async_client", lambda *a, **k: _client(msgs))
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
    monkeypatch.setattr(llm, "_async_client", lambda *a, **k: _client(msgs))
    result = await llm.structured_output("prompt", _Schema, user_id=1)
    assert result.score == 0.8 and result.reasoning == "x"


async def test_structured_output_raises_without_tool_use(monkeypatch):
    msgs = _FakeMessages(create_side_effect=[_resp(SimpleNamespace(type="text"))])
    monkeypatch.setattr(llm, "_async_client", lambda *a, **k: _client(msgs))
    with pytest.raises(ValueError):
        await llm.structured_output("prompt", _Schema, user_id=1)


async def test_structured_output_retries(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    block = SimpleNamespace(type="tool_use", name="structured_extract", input={"score": 0.5, "reasoning": "y"})
    msgs = _FakeMessages(
        create_side_effect=[anthropic.APIConnectionError(request=httpx.Request("POST", "http://t")), _resp(block)]
    )
    monkeypatch.setattr(llm, "_async_client", lambda *a, **k: _client(msgs))
    result = await llm.structured_output("prompt", _Schema, user_id=1)
    assert result.score == 0.5
    assert msgs.create_calls == 2


async def test_stream_text_raises_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    msgs = _FakeMessages(stream_side_effect=[_conn_error, _conn_error, _conn_error])
    monkeypatch.setattr(llm, "_async_client", lambda *a, **k: _client(msgs))
    with pytest.raises(anthropic.APIConnectionError):
        [tok async for tok in llm.stream_text("prompt", user_id=1)]
    assert msgs.stream_calls == 3


async def test_stream_text_refuses_over_budget_before_any_api_call(monkeypatch):
    """The F2 gate: quota runs BEFORE the call, so an over-budget user costs
    zero API traffic — not one token, not one connection attempt."""
    monkeypatch.setattr(config, "FREE_TIER_DAILY_USD", 0.0)
    msgs = _FakeMessages(stream_cm=_FakeTextStream(["never"]))
    monkeypatch.setattr(llm, "_async_client", lambda *a, **k: _client(msgs))
    with pytest.raises(QuotaExceededError):
        [tok async for tok in llm.stream_text("prompt", user_id=1)]
    assert msgs.stream_calls == 0


async def test_structured_output_refuses_over_budget_before_any_api_call(monkeypatch):
    monkeypatch.setattr(config, "FREE_TIER_DAILY_USD", 0.0)
    block = SimpleNamespace(type="tool_use", name="structured_extract", input={"score": 1, "reasoning": "z"})
    msgs = _FakeMessages(create_side_effect=[_resp(block)])
    monkeypatch.setattr(llm, "_async_client", lambda *a, **k: _client(msgs))
    with pytest.raises(QuotaExceededError):
        await llm.structured_output("prompt", _Schema, user_id=1)
    assert msgs.create_calls == 0


async def test_structured_output_raises_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    def err():
        return anthropic.APIConnectionError(request=httpx.Request("POST", "http://t"))

    msgs = _FakeMessages(create_side_effect=[err(), err(), err()])
    monkeypatch.setattr(llm, "_async_client", lambda *a, **k: _client(msgs))
    with pytest.raises(anthropic.APIConnectionError):
        await llm.structured_output("prompt", _Schema, user_id=1)
    assert msgs.create_calls == 3


# --- pre-call lookup consolidation (post-F5 cleanup) ------------------------

async def test_generation_reads_the_key_row_exactly_once(monkeypatch, meter_db):
    """resolve_api_key is the single "does this user have a key" read per call;
    resolve_model and check_quota reuse its answer instead of re-reading the
    row (the consolidation flagged in the F4 handoff)."""
    reads = {"n": 0}

    def counting_get(session, *, user_id, provider="anthropic"):
        reads["n"] += 1
        return None

    def forbidden_has(*a, **k):
        raise AssertionError("has_user_key re-read the key row — consolidation regressed")

    monkeypatch.setattr(key_store, "get_user_key", counting_get)
    monkeypatch.setattr(key_store, "has_user_key", forbidden_has)
    msgs = _FakeMessages(stream_cm=_FakeTextStream(["hi"]))
    monkeypatch.setattr(llm, "_async_client", lambda *a, **k: _client(msgs))

    [tok async for tok in llm.stream_text("prompt", user_id=1)]  # model resolves per-user

    assert reads["n"] == 1
    assert msgs.last_model == config.FREE_TIER_MODEL  # no key → free tier


async def test_key_read_failure_refuses_the_call_before_any_api_traffic(monkeypatch):
    """Pre-call reads fail closed (F2 stance): a key row we can't read is a
    call we don't make, surfaced as the refusal type callers already handle."""
    def boom(**k):
        raise RuntimeError("db down")

    monkeypatch.setattr(llm, "resolve_api_key", boom)
    msgs = _FakeMessages(stream_cm=_FakeTextStream(["never"]))
    monkeypatch.setattr(llm, "_async_client", lambda *a, **k: _client(msgs))

    with pytest.raises(QuotaExceededError):
        [tok async for tok in llm.stream_text("prompt", model="claude-haiku-4-5", user_id=1)]
    assert msgs.stream_calls == 0
