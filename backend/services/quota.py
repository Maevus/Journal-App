"""Quota gate: refuse an LLM call BEFORE it spends money (ADR 0003, freemium F2).

The mirror image of llm_client._log_usage: the meter runs after the call and
never kills it (the tokens are already paid for); this gate runs before the
call and fails closed (no money spent yet — when in doubt, don't spend).
"""
import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import Date, cast, or_
from sqlmodel import Session, func, select

import config
from clock import utcnow_naive
from models.journal import engine
from models.keys import UserApiKey
from models.usage import LLMUsage, QuotaReservation
from services import key_store

logger = logging.getLogger(__name__)

# Serializes the gate's read-then-reserve within this process; on Postgres
# _serialize_across_processes extends it across workers. See that function.
_gate_lock = threading.Lock()

# Arbitrary constant identifying the gate's Postgres advisory lock. Only has to
# be stable and not collide with another advisory lock in this database.
_GATE_LOCK_KEY = 0x51554F54  # 'QUOT'

# Sentinel for "every user" in _reserved_since — None already means system work
# there, so it can't double as the wildcard.
_ANY = object()


def _day_bucket(col):
    """Group a timestamp column by calendar day, portably across dialects.

    SQLite's `func.date(col)` returns a 'YYYY-MM-DD' string; Postgres has no
    plain `date()` scalar, so cast to DATE there instead — both land on the
    same ISO string `by_date` looks up.
    """
    if engine.dialect.name == "postgresql":
        return cast(col, Date)
    return func.date(col)


class QuotaExceededError(Exception):
    """Raised before an LLM call when the spender's daily budget is exhausted."""


def sse_quota_frame(exc: QuotaExceededError) -> str:
    """The named SSE frame both LLM streams emit on refusal — rides inside a
    200 stream because EventSource can't read a 4xx body."""
    payload = {"code": "quota_exhausted", "message": str(exc)}
    return f"event: quota\ndata: {json.dumps(payload)}\n\n"


def _utc_day_start() -> datetime:
    """Today's UTC midnight, naive — matches LLMUsage.created_at storage."""
    return utcnow_naive().replace(hour=0, minute=0, second=0, microsecond=0)


def _tier_budget(session: Session, user_id: int) -> float:
    """The keyless daily budget for `user_id`: demo budget for the demo user,
    free tier otherwise. Shared by check_quota and usage_snapshot so the two
    can't disagree."""
    from services.auth import is_demo_user_id  # local import: avoids an import cycle

    if is_demo_user_id(session, user_id):
        return config.DEMO_DAILY_USD
    return config.FREE_TIER_DAILY_USD


def _user_spend_since(session: Session, *, user_id: int, since: datetime) -> float:
    """SUM(cost) of a user's metered LLM calls since `since` (naive UTC)."""
    return session.exec(
        select(func.coalesce(func.sum(LLMUsage.cost), 0.0))
        .where(LLMUsage.user_id == user_id)
        .where(LLMUsage.created_at >= since)
    ).one()


def _reservation_floor(day_start: datetime) -> datetime:
    """The cutoff below which a reservation stops counting: the later of the
    UTC rollover (resets the budget it was taken against) and RESERVATION_TTL
    (bounds how long a dead call can hold headroom hostage).
    """
    return max(day_start, utcnow_naive() - config.RESERVATION_TTL)


def _reserved_since(session: Session, *, user_id: object, since: datetime) -> float:
    """SUM(amount) of live reservations — headroom claimed by calls in
    flight but not yet metered. `user_id` is `_ANY` for the global cap, or
    an id for one user's budget.
    """
    stmt = select(func.coalesce(func.sum(QuotaReservation.amount), 0.0)).where(
        QuotaReservation.created_at >= since
    )
    if user_id is not _ANY:
        stmt = stmt.where(QuotaReservation.user_id == user_id)
    return session.exec(stmt).one()


def _serialize_across_processes(session: Session) -> None:
    """Hold the gate's read-then-reserve against every other checker.

    The reservation only closes the read-then-act race if the SUM and the
    INSERT are one indivisible step: `_gate_lock` does that within this
    process, and on Postgres a transaction-scoped advisory lock (releases on
    commit OR rollback) extends it across the multiple workers production
    runs. One global lock, not per-user, since the global cap sums every
    user's spend and must serialize globally regardless.
    """
    if engine.dialect.name == "postgresql":
        session.exec(select(func.pg_advisory_xact_lock(_GATE_LOCK_KEY)))


def check_quota(
    *,
    user_id: Optional[int],
    on_own_key: Optional[bool] = None,
    estimated_cost: float = 0.0,
) -> Optional[int]:
    """Refuse (raise QuotaExceededError) when the user's LLM spend since UTC
    midnight has reached their daily budget. `>=`, not `>`: a $0 budget must
    refuse at $0 spent.

    `user_id` is required keyword-only; explicit None means system work,
    charged to nobody, where only the global cap applies. `on_own_key`, when
    already known to the caller, skips a redundant key-row read.

    `estimated_cost` is this call's worst case, reserved atomically alongside
    the budget check — summing only finished calls let concurrent checkers all
    read the same stale total and all pass (proven at 20 calls spending $11
    against a $1 budget). The returned reservation id must reach
    release_reservation when the call ends. Callers passing nothing reserve
    nothing, keeping the old read-only race.
    """
    day_start = _utc_day_start()
    try:
        with Session(engine) as session:
            # F4: a BYO-key user spends their own money, so neither cap
            # applies. Exempt before the gate lock — an exempt call never
            # reserves, so it need not serialize.
            if user_id is not None:
                if on_own_key is None:
                    on_own_key = key_store.has_user_key(session, user_id=user_id)
                if on_own_key:
                    return None  # nothing reserved, so nothing to release

        # Everything from here is one indivisible step, or concurrent callers
        # all measure the same stale meter and all pass.
        with _gate_lock, Session(engine) as session:
            _serialize_across_processes(session)
            since = _reservation_floor(day_start)
            # Global cap: sums spend on OUR key only — BYO users' own-key
            # spend is metered for their widget but excluded here so it can't
            # refuse everyone else (F4c). `user_id IS NULL` is always our key.
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
            global_spent += _reserved_since(session, user_id=_ANY, since=since)
            if global_spent >= config.GLOBAL_DAILY_USD:
                raise QuotaExceededError(
                    f"service-wide daily LLM cap reached (${global_spent:.4f} today)"
                )

            if user_id is not None:
                # Keyless budget (key-holders were already exempted above).
                budget = _tier_budget(session, user_id)
                spent = _user_spend_since(session, user_id=user_id, since=day_start)
                spent += _reserved_since(session, user_id=user_id, since=since)
                if spent >= budget:
                    raise QuotaExceededError(
                        f"daily LLM budget exhausted "
                        f"(${spent:.4f} of ${budget:.2f} spent today)"
                    )

            # Admitted. Claim the worst-case cost so the next checker counts
            # it, committed before returning.
            if estimated_cost > 0:
                claim = QuotaReservation(user_id=user_id, amount=estimated_cost)
                session.add(claim)
                session.commit()
                return claim.id
    except QuotaExceededError:
        raise
    except Exception as exc:
        # Fails closed: a check we can't run is a call we don't make.
        logger.exception("quota check failed — refusing the call (fails closed)")
        raise QuotaExceededError("quota check unavailable") from exc


def release_reservation(reservation_id: Optional[int], *, session: Optional[Session] = None) -> None:
    """Drop a reservation once its call is over, however it ended.

    Pass the caller's `session` to release inside the transaction that writes
    the real LLMUsage row — otherwise there's an instant where the claim is
    gone and the spend hasn't landed, and a checker in that gap sees neither.
    Never raises: a leaked reservation is survivable (RESERVATION_TTL retires
    it), so failing here must not take down the call it belongs to.
    """
    if reservation_id is None:
        return
    try:
        if session is not None:
            _delete_reservation(session, reservation_id)
            return
        with Session(engine) as own_session:
            _delete_reservation(own_session, reservation_id)
            own_session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("releasing quota reservation %s failed", reservation_id)


def _delete_reservation(session: Session, reservation_id: int) -> None:
    """Delete one reservation row, if the TTL sweep or a retry hasn't already."""
    claim = session.get(QuotaReservation, reservation_id)
    if claim is not None:
        session.delete(claim)


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
    """`user_id`'s spend: today's total + call count, tier budget and what's
    left, and a per-day breakdown for the chart.

    Counts metered spend only, not the gate's reservations — a reservation is
    a worst case usually refunded, and showing it would make the widget jump
    on every call. Trade-off: `remaining_usd` reads high while calls are in
    flight, converging once they settle. A key-holder is uncapped
    (budget/remaining None) but their spend is still metered and shown here.
    """
    day_start = _utc_day_start()
    window_start = day_start - timedelta(days=days - 1)
    with Session(engine) as session:
        on_own_key = key_store.has_user_key(session, user_id=user_id)
        keyless_budget = _tier_budget(session, user_id)  # resolved while the session is live
        spent_today = _user_spend_since(session, user_id=user_id, since=day_start)
        calls_today = session.exec(
            select(func.count(LLMUsage.id))
            .where(LLMUsage.user_id == user_id)
            .where(LLMUsage.created_at >= day_start)
        ).one()
        day = _day_bucket(LLMUsage.created_at)
        rows = session.exec(
            select(
                day,
                func.coalesce(func.sum(LLMUsage.cost), 0.0),
                func.count(LLMUsage.id),
            )
            .where(LLMUsage.user_id == user_id)
            .where(LLMUsage.created_at >= window_start)
            .group_by(day)
        ).all()

    by_date = {str(day): (spent, calls) for day, spent, calls in rows}
    daily = []
    for i in range(days):
        iso = (window_start + timedelta(days=i)).date().isoformat()
        spent, calls = by_date.get(iso, (0.0, 0))
        daily.append({"date": iso, "spent_usd": round(spent, 6), "calls": calls})

    budget = None if on_own_key else keyless_budget
    remaining = None if budget is None else round(max(0.0, budget - spent_today), 6)
    return UsageSnapshot(
        on_own_key=on_own_key,
        spent_today_usd=round(spent_today, 6),
        calls_today=calls_today,
        budget_usd=budget,
        remaining_usd=remaining,
        daily=daily,
    )
