"""Quota gate: refuse an LLM call BEFORE it spends money (ADR 0003, freemium F2).

The mirror image of llm_client._log_usage: the meter runs after the call and
never kills it (the tokens are already paid for); this gate runs before the
call and fails closed (no money spent yet — when in doubt, don't spend).
"""
import json
import logging
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


def _utc_day_start():
    """Today's UTC midnight, naive — matches LLMUsage.created_at storage."""
    return utcnow_naive().replace(hour=0, minute=0, second=0, microsecond=0)


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
            budget = config.DEMO_DAILY_USD if config.IS_DEMO else config.FREE_TIER_DAILY_USD
            spent = session.exec(
                select(func.coalesce(func.sum(LLMUsage.cost), 0.0))
                .where(LLMUsage.user_id == user_id)
                .where(LLMUsage.created_at >= day_start)
            ).one()
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
