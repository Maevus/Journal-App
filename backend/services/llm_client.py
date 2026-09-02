"""Anthropic wrapper: async streaming, Pydantic structured output,
exponential backoff (max 3 retries), per-call token + cost logging.
"""
import asyncio
import json
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
from services.quota import QuotaExceededError, check_quota, release_reservation

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger(__name__)

# Approximate pricing as ($ input, $ output) per token, keyed by model.
# Unknown models fall back to the default (opus) rates.
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


class LLMProviderError(Exception):
    """Anthropic refused the call and retrying won't fix it — a bad key, an
    exhausted credit balance, a rejected request. Carries the message the
    caller may show the user."""


def _provider_detail(exc: anthropic.APIStatusError) -> str:
    """Anthropic's own explanation, dug out of the error body."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
    return getattr(exc, "message", "") or str(exc)


def _provider_error(exc: anthropic.APIStatusError, *, on_own_key: bool) -> LLMProviderError:
    """Translate a provider failure into what this caller should be told.

    On their own key they can act on Anthropic's words. On ours they can't,
    and the operator's billing state isn't theirs to see — so the detail is
    logged and they get the generic line.
    """
    detail = _provider_detail(exc)
    logger.error("Anthropic %s: %s", exc.status_code, detail)
    if on_own_key:
        return LLMProviderError(f"Anthropic rejected the call with your API key: {detail}")
    return LLMProviderError(
        "The analysis service is unavailable right now. This is on us, not you — "
        "try again later."
    )


def sse_llm_error_frame(exc: LLMProviderError) -> str:
    """The named SSE frame both LLM streams emit when the provider fails,
    inside a 200 — a dropped connection is indistinguishable from a dead server."""
    payload = {"code": "provider_error", "message": str(exc)}
    return f"event: llm_error\ndata: {json.dumps(payload)}\n\n"


def resolve_model(*, user_id: Optional[int], on_own_key: Optional[bool] = None) -> str:
    """Which model a generation call for `user_id` runs on (ADR 0003, F3).

    Replaces the prompt-YAML model choice for user-facing generation
    (synthesis, chat, mood scoring). Task-fixed internal calls (OCR,
    date-parse, condense) pass their own model string and never reach here.

    `user_id` is required keyword-only, mirroring check_quota / _log_usage.
    None means unowned system work, which no longer generates — raises here
    rather than silently spending the app key on a model nobody owns.

    `on_own_key`: pass it if already known (resolve_api_key), to skip a
    redundant key-row read; None keeps the self-serve lookup.
    """
    if user_id is None:
        raise ValueError("no owner: unowned system work does not generate (F3)")
    from services.auth import is_demo_user_id  # local import: avoids an import cycle

    with Session(engine) as session:
        # Demo user runs the showcase on the premium model regardless of key.
        if is_demo_user_id(session, user_id):
            return config.DEMO_MODEL
        # F4: a BYO-key user pays their own way, so they get the premium model.
        if on_own_key is None:
            on_own_key = key_store.has_user_key(session, user_id=user_id)
    return config.BYO_KEY_MODEL if on_own_key else config.FREE_TIER_MODEL


def resolve_api_key(*, user_id: Optional[int]) -> Optional[str]:
    """The user's own decrypted API key, or None if they haven't set one (F4).

    None falls back to the app key (the default lives in _async_client, in
    one place). A BYO key flows straight into client construction, never logged.
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

    Uses models.list(), which authenticates but generates nothing (no output
    tokens). A rejected key raises AuthenticationError (→ False); other
    errors propagate so the caller can distinguish "bad key" from "couldn't
    check right now".
    """
    try:
        await _async_client(raw_key).models.list()
        return True
    except anthropic.AuthenticationError:
        return False


def _prompt_text(system: str, messages: list[dict]) -> str:
    """Everything we're about to send as input, flattened for token estimation."""
    return (system or "") + "".join(
        m["content"] for m in messages if isinstance(m.get("content"), str)
    )


def _worst_case_cost(model: str, *, prompt_text: str, max_tokens: int) -> float:
    """The most this call could possibly cost: input sent plus output running
    all the way to `max_tokens`. The quota gate claims this ceiling as
    headroom, erring high; the difference is handed back once the real cost
    is metered.
    """
    input_cost, output_cost = _PRICING.get(model, _DEFAULT_PRICING)
    return _estimate_tokens(prompt_text) * input_cost + max_tokens * output_cost


def _pre_call(
    model: Optional[str],
    *,
    user_id: Optional[int],
    prompt_text: str,
    max_tokens: int,
) -> tuple[str, anthropic.AsyncAnthropic, Optional[int], bool]:
    """The pre-call policy steps both entry points share, on ONE key read.

    resolve_api_key answers "does this user have a key" once; resolve_model,
    check_quota and provider-error wording reuse it instead of each re-reading
    the row (F4). Every read here runs BEFORE any API traffic and fails closed
    — a key row we can't read is a call we don't make, refused the same way
    callers already handle (the F2 stance).

    Returns the reservation id, which the caller MUST release when the call
    ends — kept as the third element so that's hard to overlook, with
    on_own_key last.
    """
    try:
        api_key = resolve_api_key(user_id=user_id)  # F4: own key if set
    except Exception as exc:
        logger.exception("key resolution failed — refusing the call (fails closed)")
        raise QuotaExceededError("key resolution unavailable") from exc
    # F3: an unspecified model resolves per-user; an explicit one (task-fixed
    # callers: OCR, date-parse, condense) is honored as-is.
    if model is None:
        model = resolve_model(user_id=user_id, on_own_key=api_key is not None)
    # F2 money gate: raises QuotaExceededError before any API traffic.
    # Pricing lives here so the gate is handed dollars, not a model string.
    reservation = check_quota(
        user_id=user_id,
        on_own_key=api_key is not None,
        estimated_cost=_worst_case_cost(model, prompt_text=prompt_text, max_tokens=max_tokens),
    )
    return model, _async_client(api_key), reservation, api_key is not None


def _estimate_tokens(text: str) -> int:
    """Rough token count (~4 chars/token) for metering a stream aborted
    before Anthropic reported real usage. Rounds up — over-charging an
    abandoned call is safer than under-charging it."""
    return max(1, (len(text) + 3) // 4)


def _log_usage(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    user_id: Optional[int],
    reservation: Optional[int] = None,
) -> None:
    """Meter one finished LLM call: operator log line + a persisted LLMUsage row.

    Both LLM entry points end here, so an unmetered call is structurally
    impossible (ADR 0003 freemium). Must never kill the call it's metering —
    tokens are already delivered and paid for, so a failed INSERT logs
    loudly and moves on (quota's F2 fails closed instead, but before the call).

    Settles the call's `reservation` in the same transaction as the row that
    supersedes it, so no concurrent checker sees neither.
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
            release_reservation(reservation, session=session)
            session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("usage meter write failed (the LLM call itself succeeded)")
        # Reservation is now the only record of this call's spend. Leave it —
        # RESERVATION_TTL retires it, erring toward over-counting.


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
    """Yield text tokens from the LLM with exponential-backoff retry.

    Pass either a single `prompt` (wrapped as one user turn) or a full
    `messages` history (multi-turn, e.g. chat) — exactly one must be given;
    `messages` wins if both are present.

    `user_id` is required keyword-only: every call states who it's billed
    to. Explicit None = system work, metered but charged to no quota.

    Retry fires on connection/rate-limit errors before the first token.
    Mid-stream errors propagate without retry — yielded tokens can't be un-sent.
    """
    if messages is None:
        if prompt is None:
            raise ValueError("stream_text requires either `prompt` or `messages`")
        messages = [{"role": "user", "content": prompt}]
    # One key read serves model resolution, the quota gate, and the client
    # (_pre_call), once per call — a retry continues the call already
    # allowed. `model` then flows to both the API call and _log_usage.
    model, client, reservation, on_own_key = _pre_call(
        model,
        user_id=user_id,
        prompt_text=_prompt_text(system, messages),
        max_tokens=max_tokens,
    )
    last_err: Exception | None = None

    # A hang-up mid-stream unwinds this generator at `yield`, so
    # get_final_message() never runs and the call would meter as $0 while
    # Anthropic still bills it — bypassing the daily caps by disconnecting
    # early. The partial is metered from a finally instead, erring high.
    metered = False
    started = False
    delivered: list[str] = []

    def _meter_partial() -> None:
        _log_usage(
            model,
            _estimate_tokens(_prompt_text(system, messages)),
            _estimate_tokens("".join(delivered)),
            user_id=user_id,
            reservation=reservation,
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
                        model,
                        msg.usage.input_tokens,
                        msg.usage.output_tokens,
                        user_id=user_id,
                        reservation=reservation,
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
            except anthropic.APIStatusError as exc:
                # Ordered after _RETRYABLE, which RateLimitError also matches —
                # the retry branch has to claim it first.
                raise _provider_error(exc, on_own_key=on_own_key) from exc

        raise last_err  # type: ignore[misc]
    finally:
        # Three ways out, reservation settled on all: `metered` means
        # _log_usage already released it; started-but-unmetered is the abort
        # path (partial metering does the same); never-started just hands
        # the claim back.
        if started and not metered:
            _meter_partial()
        elif not metered:
            release_reservation(reservation)


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
    """Return a Pydantic model instance validated against the LLM's tool-use
    output. Retries with exponential backoff on transient errors.

    `user_id`: required keyword-only billing attribution — see stream_text.
    """
    # One key read serves all three pre-call policy steps — see _pre_call.
    model, client, reservation, on_own_key = _pre_call(
        model,
        user_id=user_id,
        prompt_text=(system or "") + prompt,
        max_tokens=max_tokens,
    )
    tool_name = "structured_extract"
    tool_def = {
        "name": tool_name,
        "description": "Return structured data that exactly matches the provided schema.",
        "input_schema": output_schema.model_json_schema(),
    }

    metered = False
    try:
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
                    model,
                    response.usage.input_tokens,
                    response.usage.output_tokens,
                    user_id=user_id,
                    reservation=reservation,
                )
                metered = True

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
            except anthropic.APIStatusError as exc:
                # See stream_text: this must sit after the retryable branch.
                raise _provider_error(exc, on_own_key=on_own_key) from exc

        raise RuntimeError("structured_output exhausted retries without raising")
    finally:
        # Exhausted retries or a schema that never validated leaves here
        # unmetered — hand the claim back rather than let it sit until the TTL.
        if not metered:
            release_reservation(reservation)
