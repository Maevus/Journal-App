"""Quota gate: refuse an LLM call BEFORE it spends money (ADR 0003, freemium F2).

The mirror image of llm_client._log_usage: the meter runs after the call and
never kills it (the tokens are already paid for); this gate runs before the
call and fails closed (no money spent yet — when in doubt, don't spend).
"""
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import or_
from sqlmodel import Session, func, select

import config
from clock import utcnow_naive
from models.journal import engine
from models.keys import UserApiKey
from models.usage import LLMUsage
from services import key_store

logger = logging.getLogger(__name__)


class QuotaExceededError(Exception):
    """Raised before an LLM call when the spender's daily budget is exhausted."""


def sse_quota_frame(exc: QuotaExceededError) -> str:
    """The named SSE frame both LLM streams emit on refusal (decided
    2026-07-13): the refusal rides inside the 200 stream because EventSource
    can't read a 4xx body. Lives next to the exception so the wire payload
    the frontend parses has exactly one definition."""
    payload = {"code": "quota_exhausted", "message": str(exc)}
    return f"event: quota\ndata: {json.dumps(payload)}\n\n"


def _utc_day_start() -> datetime:
    """Today's UTC midnight, naive — matches LLMUsage.created_at storage."""
    return utcnow_naive().replace(hour=0, minute=0, second=0, microsecond=0)


def _tier_budget() -> float:
    """The keyless daily budget for the current mode — the value the gate
    enforces for a user without their own key (demo mode vs the free tier).
    Shared by check_quota and usage_snapshot so the two can't disagree."""
    return config.DEMO_DAILY_USD if config.IS_DEMO else config.FREE_TIER_DAILY_USD


def _user_spend_since(session: Session, *, user_id: int, since: datetime) -> float:
    """SUM(cost) of a user's metered LLM calls since `since` (naive UTC)."""
    return session.exec(
        select(func.coalesce(func.sum(LLMUsage.cost), 0.0))
        .where(LLMUsage.user_id == user_id)
        .where(LLMUsage.created_at >= since)
    ).one()


def check_quota(*, user_id: Optional[int]) -> None:
    """Refuse (raise QuotaExceededError) when the user's LLM spend since UTC
    midnight has reached their daily budget. `>=`, not `>`: a $0 budget must
    refuse at $0 spent — that's the keyless launch posture.

    `user_id` is required keyword-only (the house pattern): every check must
    state whose budget it's spending against. Explicit None = system work
    (the user-less startup-sync scoring): charged to nobody's budget, so the
    per-user check doesn't apply — only the global cap can stop it.
    """
    day_start = _utc_day_start()
    try:
        with Session(engine) as session:
            # F4: a user on their own key spends their own money on their own
            # key — neither cap applies (both protect OUR key). Exempt them
            # before touching either cap.
            if user_id is not None and key_store.has_user_key(session, user_id=user_id):
                return
            # Global cap — the kill-switch bounding the worst day our key can
            # have, so it applies to every call, system work included. It sums
            # only spend on OUR key: BYO users' own-key spend is metered for
            # their usage widget but must not refuse everyone else (F4c), so
            # rows from key-holders are excluded. `user_id IS NULL` (system
            # work) is always on our key, so it counts.
            global_spent = session.exec(
                select(func.coalesce(func.sum(LLMUsage.cost), 0.0))
                .where(LLMUsage.created_at >= day_start)
                .where(
                    or_(
                        LLMUsage.user_id.is_(None),
                        LLMUsage.user_id.not_in(select(UserApiKey.user_id)),
                    )
                )
            ).one()
            if global_spent >= config.GLOBAL_DAILY_USD:
                raise QuotaExceededError(
                    f"service-wide daily LLM cap reached (${global_spent:.4f} today)"
                )

            if user_id is None:
                return
            # In demo mode every visitor IS the seeded demo user, so the mode,
            # not the user row, decides the budget. Per-user tiers arrive with
            # BYO keys (F4); until then this pair of config knobs is the whole
            # budget model.
            budget = _tier_budget()
            spent = _user_spend_since(session, user_id=user_id, since=day_start)
        if spent >= budget:
            raise QuotaExceededError(
                f"daily LLM budget exhausted (${spent:.4f} of ${budget:.2f} spent today)"
            )
    except QuotaExceededError:
        raise
    except Exception as exc:
        # Fails closed: a check we can't run is a call we don't make. Callers
        # get the same refusal type they already handle.
        logger.exception("quota check failed — refusing the call (fails closed)")
        raise QuotaExceededError("quota check unavailable") from exc


@dataclass
class UsageSnapshot:
    """A read-only view of one user's LLM spend, for the usage widget (F5)."""

    on_own_key: bool
    spent_today_usd: float
    calls_today: int
    budget_usd: Optional[float]  # None = uncapped (own key)
    remaining_usd: Optional[float]  # None = uncapped
    daily: list[dict]  # [{"date", "spent_usd", "calls"}], oldest → newest


def usage_snapshot(*, user_id: int, days: int = 30) -> UsageSnapshot:
    """`user_id`'s spend, read from the same meter table check_quota gates on
    (via the module `engine`, not an injected session), so the widget can never
    drift from the gate: today's spend + call count, the tier budget and what's
    left, and a per-day breakdown for the chart.

    A key-holder is uncapped (budget/remaining None) — their own-key spend is
    still metered and shown here, it just gates nobody (F4). Spend is scoped to
    this user (LLMUsage.user_id) so one user never sees another's numbers.
    """
    day_start = _utc_day_start()
    window_start = day_start - timedelta(days=days - 1)
    with Session(engine) as session:
        on_own_key = key_store.has_user_key(session, user_id=user_id)
        spent_today = _user_spend_since(session, user_id=user_id, since=day_start)
        calls_today = session.exec(
            select(func.count(LLMUsage.id))
            .where(LLMUsage.user_id == user_id)
            .where(LLMUsage.created_at >= day_start)
        ).one()
        rows = session.exec(
            select(
                func.date(LLMUsage.created_at),
                func.coalesce(func.sum(LLMUsage.cost), 0.0),
                func.count(LLMUsage.id),
            )
            .where(LLMUsage.user_id == user_id)
            .where(LLMUsage.created_at >= window_start)
            .group_by(func.date(LLMUsage.created_at))
        ).all()

    by_date = {str(day): (spent, calls) for day, spent, calls in rows}
    daily = []
    for i in range(days):
        iso = (window_start + timedelta(days=i)).date().isoformat()
        spent, calls = by_date.get(iso, (0.0, 0))
        daily.append({"date": iso, "spent_usd": round(spent, 6), "calls": calls})

    budget = None if on_own_key else _tier_budget()
    remaining = None if budget is None else round(max(0.0, budget - spent_today), 6)
    return UsageSnapshot(
        on_own_key=on_own_key,
        spent_today_usd=round(spent_today, 6),
        calls_today=calls_today,
        budget_usd=budget,
        remaining_usd=remaining,
        daily=daily,
    )
