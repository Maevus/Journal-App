"""Persisted LLM usage meter (ADR 0003, freemium phase)."""
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from clock import utcnow_naive


class LLMUsage(SQLModel, table=True):
    """One LLM call = one row, written by llm_client._log_usage — the choke
    point both LLM entry points already funnel through, so an unmetered call
    is impossible (the spend-side twin of user_entries for reads).

    `user_id` None = system work (the user-less startup-sync mood scoring):
    cost visibility, not anyone's quota. Quota checks (F2) sum `cost` per user
    over `created_at >= today's UTC midnight` — both columns are indexed for
    that query. Provider-agnostic on purpose: `model` is a plain string, so
    non-Anthropic models are new rows, not new schema.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, index=True)
    model: str
    input_tokens: int
    output_tokens: int
    cost: float  # dollars, computed from the pricing table at call time
    created_at: datetime = Field(default_factory=utcnow_naive, index=True)
