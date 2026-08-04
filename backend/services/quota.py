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

    SQLite has no date type, so `func.date(col)` returns a 'YYYY-MM-DD' string —
    the app's convention. Postgres has no plain `date()` scalar, so cast to DATE
    there (the driver hands back a `datetime.date`, whose `str()` is the same ISO
    string). Both feed the same `by_date` key that daily's `.date().isoformat()`
    looks up. The SELECT and GROUP BY must use the identical expression.
    """
    if engine.dialect.name == "postgresql":
        return cast(col, Date)
    return func.date(col)


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


def _tier_budget(session: Session, user_id: int) -> float:
    """The keyless daily budget for `user_id` — the demo user gets the demo
    budget, everyone else the free tier. Per-user (not a global mode) since one
    app now serves the demo visitor and real users at once. Shared by check_quota
    and usage_snapshot so the two can't disagree."""
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
    """The cutoff below which a reservation stops counting.

    Two things retire one: the UTC rollover that resets the budget it was taken
    against, and RESERVATION_TTL, which bounds how long a call that died before
    settling can hold headroom hostage. Whichever is later wins.
    """
    return max(day_start, utcnow_naive() - config.RESERVATION_TTL)


def _reserved_since(session: Session, *, user_id: object, since: datetime) -> float:
    """SUM(amount) of live reservations — headroom claimed by calls that are
    in flight but not yet metered. `user_id` is `_ANY` for the global cap
    (every reservation is spend on our key), or an id for one user's budget.
    """
    stmt = select(func.coalesce(func.sum(QuotaReservation.amount), 0.0)).where(
        QuotaReservation.created_at >= since
    )
    if user_id is not _ANY:
        stmt = stmt.where(QuotaReservation.user_id == user_id)
    return session.exec(stmt).one()


def _serialize_across_processes(session: Session) -> None:
    """Hold the gate's read-then-reserve against every other checker.

    The reservation only closes the race if the SUM and the INSERT are one
    indivisible step. `_gate_lock` does that within this process; on Postgres,
    where production runs several workers, an advisory lock extends it across
    them. It is transaction-scoped, so it releases on commit OR rollback —
    including the rollback the fail-closed path takes.

    One global lock, not one per user: the global cap sums every user's spend,
    so it has to serialize globally regardless. The critical section is a
    couple of indexed aggregates, so the contention is not worth splitting.
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
    refuse at $0 spent — that's the keyless launch posture.

    `user_id` is required keyword-only (the house pattern): every check must
    state whose budget it's spending against. Explicit None = system work
    (the user-less startup-sync scoring): charged to nobody's budget, so the
    per-user check doesn't apply — only the global cap can stop it.

    `on_own_key` is the post-F5 lookup consolidation: the llm_client entry
    points already read the user's key row (resolve_api_key), so they pass its
    presence here instead of this gate re-reading the same row. None (the
    default, for callers that haven't looked) keeps the self-serve lookup.

    `estimated_cost` is what this call might cost at worst. Summing the meter
    alone only sees calls that have already finished, so concurrent checkers
    all read the same stale total and all pass — proven at 20 calls spending
    $11 against a $1 budget. Declaring the worst case makes the admitted call
    visible to the next checker immediately: it is reserved in the same
    serialized transaction as the sums, and the returned id must be handed to
    release_reservation when the call ends so the unspent part goes back.
    Callers that pass nothing reserve nothing — they keep the old read-only
    behavior, and the old race with it.
    """
    day_start = _utc_day_start()
    try:
        with Session(engine) as session:
            # F4: a user on their own key spends their own money on their own
            # key — neither cap applies (both protect OUR key). Exempt them
            # before touching either cap, and before taking the gate lock: an
            # exempt call never reserves, so it need not serialize.
            if user_id is not None:
                if on_own_key is None:
                    on_own_key = key_store.has_user_key(session, user_id=user_id)
                if on_own_key:
                    return None  # nothing reserved, so nothing to release

        # Everything from here — both sums, both comparisons, and the
        # reservation — is one indivisible step, or concurrent callers all
        # measure the same stale meter and all pass.
        with _gate_lock, Session(engine) as session:
            _serialize_across_processes(session)
            since = _reservation_floor(day_start)
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
            global_spent += _reserved_since(session, user_id=_ANY, since=since)
            if global_spent >= config.GLOBAL_DAILY_USD:
                raise QuotaExceededError(
                    f"service-wide daily LLM cap reached (${global_spent:.4f} today)"
                )

            if user_id is not None:
                # Keyless budget for this user: the demo user gets the demo
                # budget, everyone else the free tier (per-user, resolved from
                # the same session). A key-holder was already exempted above.
                budget = _tier_budget(session, user_id)
                spent = _user_spend_since(session, user_id=user_id, since=day_start)
                spent += _reserved_since(session, user_id=user_id, since=since)
                if spent >= budget:
                    raise QuotaExceededError(
                        f"daily LLM budget exhausted "
                        f"(${spent:.4f} of ${budget:.2f} spent today)"
                    )

            # Admitted. Claim this call's worst-case cost so the next checker
            # counts it — committed before we return, so no call escapes the
            # window between passing the gate and being metered.
            if estimated_cost > 0:
                claim = QuotaReservation(user_id=user_id, amount=estimated_cost)
                session.add(claim)
                session.commit()
                return claim.id
    except QuotaExceededError:
        raise
    except Exception as exc:
        # Fails closed: a check we can't run is a call we don't make. Callers
        # get the same refusal type they already handle.
        logger.exception("quota check failed — refusing the call (fails closed)")
        raise QuotaExceededError("quota check unavailable") from exc


def release_reservation(reservation_id: Optional[int], *, session: Optional[Session] = None) -> None:
    """Drop a reservation once its call is over, however it ended.

    The mirror of check_quota's claim, and the reason a worst-case estimate is
    safe to charge: the over-claim is only held for the life of the call. Pass
    the caller's `session` to release inside the transaction that writes the
    real LLMUsage row — otherwise there is an instant where the claim is gone
    and the spend has not landed, and a checker in that gap sees neither.

    Never raises. A leaked reservation is already survivable (RESERVATION_TTL
    retires it), so failing here must not take down the call it belongs to —
    the same stance as the meter write it rides along with.
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
    """Delete one reservation row, if it is still there (the TTL sweep or a
    retry may have got there first)."""
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
    """`user_id`'s spend, read from the same meter table check_quota gates on
    (via the module `engine`, not an injected session): today's spend + call
    count, the tier budget and what's left, and a per-day breakdown for the chart.

    Deliberately counts metered spend only, not the reservations the gate also
    sums. This is a statement about money actually spent — a reservation is a
    worst case that is usually refunded, and showing it would make the widget
    jump up and drop back on every call. The cost is that while calls are in
    flight `remaining_usd` reads higher than what the gate will actually admit;
    it converges the moment they settle.

    A key-holder is uncapped (budget/remaining None) — their own-key spend is
    still metered and shown here, it just gates nobody (F4). Spend is scoped to
    this user (LLMUsage.user_id) so one user never sees another's numbers.
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
