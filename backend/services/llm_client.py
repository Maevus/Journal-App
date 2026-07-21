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
from services.quota import check_quota

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger(__name__)

# Approximate pricing as ($ input, $ output) per token, keyed by model. Unknown
# models fall back to the default (opus) rates. This whole table becomes per-user
# config once users bring their own API key and choose their own model.
_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.0 / 1_000_000, 25.0 / 1_000_000),  # $5 in / $25 out per 1M
    "claude-haiku-4-5": (1.0 / 1_000_000, 5.0 / 1_000_000),  # $1 in / $5 out per 1M
}
_DEFAULT_PRICING = _PRICING["claude-opus-4-8"]

_RETRYABLE = (
    anthropic.RateLimitError,
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
)


def resolve_model(*, user_id: Optional[int]) -> str:
    """Which model a generation call for `user_id` runs on (ADR 0003, F3).

    The per-user resolution step that replaces the prompt-YAML model choice
    for user-facing generation (synthesis, chat, mood scoring).
    Task-fixed internal calls (OCR, date-parse, condense) still pass their own
    model string and never reach here.

    `user_id` is required keyword-only, mirroring check_quota / _log_usage, so
    resolution joins quota and metering as the third per-call step. None means
    unowned system work, which no longer generates — it raises here rather than
    silently spending the app key on a model nobody owns.
    """
    if user_id is None:
        raise ValueError("no owner: unowned system work does not generate (F3)")
    # In demo mode every visitor is the seeded demo user, so the mode picks the
    # model. Per-user model config (a user's own key/model) arrives with F4.
    return config.DEMO_MODEL if config.IS_DEMO else config.FREE_TIER_MODEL


def _async_client() -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


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
    # F3: an unspecified model resolves per-user (free→haiku, demo→opus). An
    # explicit model (task-fixed callers: OCR, date-parse, condense) is honored.
    # The one `model` variable then flows to both the API call and _log_usage,
    # so what's billed is always what ran.
    if model is None:
        model = resolve_model(user_id=user_id)
    # The F2 money gate: raises QuotaExceededError before any API traffic.
    # Once per call, not per retry — a retry continues the call already allowed.
    check_quota(user_id=user_id)
    client = _async_client()
    last_err: Exception | None = None

    for attempt in range(max_retries):
        try:
            async with client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
            ) as stream:
                async for chunk in stream.text_stream:
                    yield chunk
                msg = await stream.get_final_message()
                _log_usage(model, msg.usage.input_tokens, msg.usage.output_tokens, user_id=user_id)
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
    if model is None:  # F3: resolve per-user; see stream_text
        model = resolve_model(user_id=user_id)
    check_quota(user_id=user_id)  # the F2 money gate — see stream_text
    client = _async_client()
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
