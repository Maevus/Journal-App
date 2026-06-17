"""
Anthropic wrapper: async streaming, Pydantic structured output,
exponential backoff (max 3 retries), per-call token + cost logging.
"""
import asyncio
import logging
import os
from typing import AsyncGenerator, Type, TypeVar

import anthropic
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-4-8"

# Approximate pricing for claude-opus-4-8 ($/token; $5 in / $25 out per 1M).
# Keep in sync with DEFAULT_MODEL — both become per-user config once users bring
# their own API key and choose their own model.
_INPUT_COST_PER_TOKEN = 5.0 / 1_000_000
_OUTPUT_COST_PER_TOKEN = 25.0 / 1_000_000

_RETRYABLE = (
    anthropic.RateLimitError,
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
)


def _async_client() -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def _log_usage(model: str, input_tokens: int, output_tokens: int) -> None:
    cost = input_tokens * _INPUT_COST_PER_TOKEN + output_tokens * _OUTPUT_COST_PER_TOKEN
    print(
        f"[llm] model={model} input_tokens={input_tokens} "
        f"output_tokens={output_tokens} cost=${cost:.5f}"
    )


async def stream_text(
    prompt: str,
    system: str = "",
    model: str = DEFAULT_MODEL,
    max_tokens: int = 1024,
    max_retries: int = 3,
) -> AsyncGenerator[str, None]:
    """
    Yield text tokens from the LLM with exponential-backoff retry.

    Retry fires on connection/rate-limit errors before the first token is
    delivered.  Mid-stream errors propagate without retry — tokens already
    yielded cannot be un-sent.
    """
    client = _async_client()
    last_err: Exception | None = None

    for attempt in range(max_retries):
        try:
            async with client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                async for chunk in stream.text_stream:
                    yield chunk
                msg = await stream.get_final_message()
                _log_usage(model, msg.usage.input_tokens, msg.usage.output_tokens)
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
    model: str = DEFAULT_MODEL,
    max_tokens: int = 512,
    max_retries: int = 3,
) -> T:
    """
    Return a Pydantic model instance validated against the LLM's tool-use output.
    Retries with exponential backoff on transient errors.
    """
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
            _log_usage(model, response.usage.input_tokens, response.usage.output_tokens)

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
