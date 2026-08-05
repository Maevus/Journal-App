"""Persisted LLM usage meter (ADR 0003, freemium phase)."""
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from clock import utcnow_naive


class LLMUsage(SQLModel, table=True):
    """One LLM call = one row, written by llm_client._log_usage — the choke
    point both LLM entry points funnel through, so an unmetered call is
    impossible (spend-side twin of user_entries).

    `user_id` None = system work (user-less startup-sync scoring): cost
    visibility, not quota. Quota checks (F2) sum `cost` per user since today's
    UTC midnight — both columns indexed for that. `model` is a plain string,
    so a new provider is a new row, not new schema.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, index=True)
    model: str
    input_tokens: int
    output_tokens: int
    cost: float  # dollars, computed from the pricing table at call time
    created_at: datetime = Field(default_factory=utcnow_naive, index=True)


class QuotaReservation(SQLModel, table=True):
    """Headroom claimed by an LLM call that's started but not yet metered.

    LLMUsage rows exist only once a call finishes, so N concurrent checks
    against it alone would all see an empty meter and pass. This reservation is
    written inside the same serialized transaction as that sum, so the next
    checker sees the in-flight call before it's billed.

    `amount` is worst-case cost, settled to the true cost (and refunded) when
    the call ends. Rows older than RESERVATION_TTL are ignored so a call that
    died before settling can't strand headroom forever. BYO users never
    reserve — only OUR key's spend is bounded here.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, index=True)
    amount: float  # dollars, the call's worst case
    created_at: datetime = Field(default_factory=utcnow_naive, index=True)
