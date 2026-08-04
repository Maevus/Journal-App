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


class QuotaReservation(SQLModel, table=True):
    """Headroom claimed by an LLM call that has started but not yet been metered.

    LLMUsage only exists once a call has *finished*, so a gate that sums it
    alone sees nothing of the calls already in flight — N concurrent checks all
    read the same empty meter and all pass. A reservation is written inside the
    same serialized transaction as that sum, so the next checker sees this call
    even though it hasn't been billed yet, and check_quota's comparison keeps
    its ordinary `spent >= budget` shape.

    `amount` is the call's worst-case cost, not its real one: the gate has to
    commit before the tokens exist. llm_client settles the row against the true
    cost once the call ends, so the over-estimate is refunded rather than kept.
    A reservation whose call died before settling would strand that headroom
    forever, so sums ignore rows older than RESERVATION_TTL.

    BYO users are exempted before either cap is consulted and so never reserve —
    every row here is spend on OUR key, which is what both caps bound.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, index=True)
    amount: float  # dollars, the call's worst case
    created_at: datetime = Field(default_factory=utcnow_naive, index=True)
