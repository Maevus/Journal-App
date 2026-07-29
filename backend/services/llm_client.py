"""
Anthropic wrapper: async streaming, Pydantic structured output,
exponential backoff (max 3 retries), per-call token + cost logging.
"""
import asyncio
import logging
import os
from typing import AsyncGenerator, Optional, Type, TypeVar

import anthropic
from pydantic import BaseModel
from sqlmodel import Session

import config
from models.journal import engine
from models.usage import LLMUsage
from services import key_store
from services.quota import QuotaExceededError, check_quota

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger(__name__)

# Approximate pricing as ($ input, $ output) per token, keyed by model. Unknown
# models fall back to the default (opus) rates. This whole table becomes per-user
# config once users bring their own API key and choose their own model.
_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.0 / 1_000_000, 25.0 / 1_000_000),  # $5 in / $25 out per 1M
    "claude-haiku-4-5": (1.0 / 1_000_000, 5.0 / 1_000_000),  # $1 in / $5 out per 1M
    "claude-sonnet-4-6": (3.0 / 1_000_000, 15.0 / 1_000_000),  # $3 in / $15 out per 1M (OCR)
}
_DEFAULT_PRICING = _PRICING["claude-opus-4-8"]

_RETRYABLE = (
    anthropic.RateLimitError,
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
)


def resolve_model(*, user_id: Optional[int], on_own_key: Optional[bool] = None) -> str:
    """Which model a generation call for `user_id` runs on (ADR 0003, F3).

    The per-user resolution step that replaces the prompt-YAML model choice
    for user-facing generation (synthesis, chat, mood scoring).
    Task-fixed internal calls (OCR, date-parse, condense) still pass their own
    model string and never reach here.

    `user_id` is required keyword-only, mirroring check_quota / _log_usage, so
    resolution joins quota and metering as the third per-call step. None means
    unowned system work, which no longer generates — it raises here rather than
    silently spending the app key on a model nobody owns.

    `on_own_key`: the post-F5 lookup consolidation — an entry point that has
    already read the key row (resolve_api_key) passes its answer; None (routers,
    which haven't looked) keeps the self-serve lookup.
    """
    if user_id is None:
        raise ValueError("no owner: unowned system work does not generate (F3)")
    from services.auth import is_demo_user_id  # local import: avoids an import cycle

    with Session(engine) as session:
        # The demo user runs the showcase on the premium model regardless of key.
        if is_demo_user_id(session, user_id):
            return config.DEMO_MODEL
        # F4: a user on their own key pays their own way, so they get the premium
        # model, not the free-tier default — the first-class account payoff.
        if on_own_key is None:
            on_own_key = key_store.has_user_key(session, user_id=user_id)
    return config.BYO_KEY_MODEL if on_own_key else config.FREE_TIER_MODEL


def resolve_api_key(*, user_id: Optional[int]) -> Optional[str]:
    """The user's own decrypted API key, or None if they haven't set one (F4).

    None means "fall back to the app key" — the env fallback lives in
    _async_client so the app-key default is defined in exactly one place. A BYO
    key flows straight into client construction and is never logged.
    """
    if user_id is None:
        return None
    with Session(engine) as session:
        return key_store.get_user_key(session, user_id=user_id)


def _async_client(api_key: Optional[str] = None) -> anthropic.AsyncAnthropic:
    """Build the SDK client on the given key, or the app env key if None."""
    return anthropic.AsyncAnthropic(api_key=api_key or os.environ["ANTHROPIC_API_KEY"])


async def key_is_valid(raw_key: str) -> bool:
    """Whether a provider key authenticates (F4 — validate a BYO key on save).

    Uses models.list(), which authenticates the key but generates nothing, so
    validation costs no output tokens. A rejected key raises AuthenticationError
    (→ False); other errors (network, rate limit) propagate so the caller can
    distinguish "bad key" from "couldn't check right now".
    """
    try:
        await _async_client(raw_key).models.list()
        return True
    except anthropic.AuthenticationError:
        return False


def _pre_call(
    model: Optional[str], *, user_id: Optional[int]
) -> tuple[str, anthropic.AsyncAnthropic]:
    """The pre-call policy steps both entry points share, on ONE key read.

    resolve_api_key is the single "does this user have a key" read; resolve_model
    (when the model isn't task-fixed) and check_quota reuse its answer instead of
    each re-reading the row (the consolidation flagged at F4). Every read here
    runs BEFORE any API traffic and fails closed — a key row we can't read is a
    call we don't make, surfaced as the same refusal type callers already handle
    (the F2 stance; this also turns an APP_ENCRYPTION_KEY misconfiguration into
    a clean refusal instead of a mid-stream 500).
    """
    try:
        api_key = resolve_api_key(user_id=user_id)  # F4: own key if set
    except Exception as exc:
        logger.exception("key resolution failed — refusing the call (fails closed)")
        raise QuotaExceededError("key resolution unavailable") from exc
    # F3: an unspecified model resolves per-user (free→haiku, demo→opus). An
    # explicit model (task-fixed callers: OCR, date-parse, condense) is honored.
    if model is None:
        model = resolve_model(user_id=user_id, on_own_key=api_key is not None)
    # The F2 money gate: raises QuotaExceededError before any API traffic.
    check_quota(user_id=user_id, on_own_key=api_key is not None)
    return model, _async_client(api_key)


def _estimate_tokens(text: str) -> int:
    """Rough token count (~4 chars/token) for metering a stream that was aborted
    before Anthropic reported real usage. Rounds up — over-charging an abandoned
    call is safer than under-charging it."""
    return max(1, (len(text) + 3) // 4)


def _log_usage(model: str, input_tokens: int, output_tokens: int, *, user_id: Optional[int]) -> None:
    """Meter one finished LLM call: operator log line + a persisted LLMUsage row.

    Both LLM entry points end here, so this is the spend choke point — an
    unmetered call is structurally impossible (ADR 0003 freemium). The meter
    write must never kill the call it's metering: the tokens are already
    delivered (and paid for), so a failed INSERT logs loudly and moves on.
    Quota enforcement (F2) fails closed instead — but it runs BEFORE the call.
    """
    input_cost, output_cost = _PRICING.get(model, _DEFAULT_PRICING)
    cost = input_tokens * input_cost + output_tokens * output_cost
    print(
        f"[llm] user={user_id} model={model} input_tokens={input_tokens} "
        f"output_tokens={output_tokens} cost=${cost:.5f}"
    )
    try:
        with Session(engine) as session:
            session.add(
                LLMUsage(
                    user_id=user_id,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost=cost,
                )
            )
            session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("usage meter write failed (the LLM call itself succeeded)")


async def stream_text(
    prompt: str | None = None,
    system: str = "",
    model: str | None = None,
    max_tokens: int = 1024,
    max_retries: int = 3,
    messages: list[dict] | None = None,
    *,
    user_id: Optional[int],
) -> AsyncGenerator[str, None]:
    """
    Yield text tokens from the LLM with exponential-backoff retry.

    Pass either a single `prompt` (wrapped as one user turn) or a full
    `messages` history (for multi-turn callers like chat). Exactly one of the
    two must be given; `messages` wins if both are present.

    `user_id` is required keyword-only (the house pattern): every call must
    state who it's billed to. Explicit None = system work — metered for
    visibility, charged to nobody's quota.

    Retry fires on connection/rate-limit errors before the first token is
    delivered.  Mid-stream errors propagate without retry — tokens already
    yielded cannot be un-sent.
    """
    if messages is None:
        if prompt is None:
            raise ValueError("stream_text requires either `prompt` or `messages`")
        messages = [{"role": "user", "content": prompt}]
    # One key read serves model resolution, the quota gate, and the client (see
    # _pre_call). Runs once per call, not per retry — a retry continues the call
    # already allowed. The one `model` variable then flows to both the API call
    # and _log_usage, so what's billed is always what ran.
    model, client = _pre_call(model, user_id=user_id)
    last_err: Exception | None = None

    # A client that hangs up mid-stream unwinds this generator at `yield`, so
    # get_final_message() is never reached and the call would be metered as $0
    # while Anthropic still bills it. That makes DEMO_DAILY_USD and
    # GLOBAL_DAILY_USD bypassable by disconnecting early, so the partial has to
    # be metered from a finally. The estimate deliberately errs high: charging
    # slightly too much for an abandoned call is the safe direction.
    metered = False
    started = False
    delivered: list[str] = []

    def _meter_partial() -> None:
        prompt_text = (system or "") + "".join(
            m["content"] for m in messages if isinstance(m.get("content"), str)
        )
        _log_usage(
            model,
            _estimate_tokens(prompt_text),
            _estimate_tokens("".join(delivered)),
            user_id=user_id,
        )

    try:
        for attempt in range(max_retries):
            try:
                async with client.messages.stream(
                    model=model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=messages,
                ) as stream:
                    started = True
                    async for chunk in stream.text_stream:
                        delivered.append(chunk)
                        yield chunk
                    msg = await stream.get_final_message()
                    _log_usage(
                        model, msg.usage.input_tokens, msg.usage.output_tokens, user_id=user_id
                    )
                    metered = True
                    return
            except _RETRYABLE as exc:
                last_err = exc
                if attempt < max_retries - 1:
                    delay = 2**attempt
                    logger.warning(
                        "LLM stream attempt %d/%d failed (%s), retrying in %ds",
                        attempt + 1,
                        max_retries,
                        type(exc).__name__,
                        delay,
                    )
                    await asyncio.sleep(delay)

        raise last_err  # type: ignore[misc]
    finally:
        if started and not metered:
            _meter_partial()


async def structured_output(
    prompt: str,
    output_schema: Type[T],
    system: str = "",
    model: str | None = None,
    max_tokens: int = 512,
    max_retries: int = 3,
    *,
    user_id: Optional[int],
) -> T:
    """
    Return a Pydantic model instance validated against the LLM's tool-use output.
    Retries with exponential backoff on transient errors.

    `user_id`: required keyword-only billing attribution — see stream_text.
    """
    # One key read serves all three pre-call policy steps — see _pre_call.
    model, client = _pre_call(model, user_id=user_id)
    tool_name = "structured_extract"
    tool_def = {
        "name": tool_name,
        "description": "Return structured data that exactly matches the provided schema.",
        "input_schema": output_schema.model_json_schema(),
    }

    for attempt in range(max_retries):
        try:
            response = await client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                tools=[tool_def],
                tool_choice={"type": "tool", "name": tool_name},
            )
            _log_usage(
                model, response.usage.input_tokens, response.usage.output_tokens, user_id=user_id
            )

            for block in response.content:
                if block.type == "tool_use" and block.name == tool_name:
                    return output_schema.model_validate(block.input)

            raise ValueError("No tool_use block returned by the model")

        except _RETRYABLE as exc:
            if attempt == max_retries - 1:
                raise
            delay = 2**attempt
            logger.warning(
                "LLM structured attempt %d/%d failed (%s), retrying in %ds",
                attempt + 1,
                max_retries,
                type(exc).__name__,
                delay,
            )
            await asyncio.sleep(delay)

    raise RuntimeError("structured_output exhausted retries without raising")
