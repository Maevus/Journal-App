"""Per-identity rate limiting: how often, not how much (services/quota.py
bounds spend; BYO-key users are exempt there but not here — this protects
the process, not the wallet).

Route dependency, not middleware (middleware sits outside CORSMiddleware, so
429s would lose their CORS header — test_rate_limit_routes.py pins this).
Named tiers keep budgets in one config table. Fails open on a broken store,
logging loudly, unlike quota's fail-closed.
"""
import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import delete
from sqlmodel import Session

import config
from clock import utcnow_naive
from models.journal import get_session
from models.rate_limit import RateLimitCounter

logger = logging.getLogger(__name__)

# The route policy, as names. Each tier maps to a pair of config knobs below.
TIER_CHAT = "chat"
TIER_SYNTHESIS = "synthesis"
TIER_INGEST = "ingest"
TIER_KEYS = "keys"
TIER_SEARCH = "search"
TIER_AUTH = "auth"

# (bucket-key suffix, refusal-message word, seconds). Shortest window first:
# a caller stopped by the minute limit shouldn't also burn its hourly budget.
_WINDOWS: tuple[tuple[str, str, int], ...] = (
    ("min", "minute", 60),
    ("hr", "hour", 3600),
)

# Pruned opportunistically on the request path (no scheduler dependency),
# at most this often per process — a cheap indexed delete either way.
_SWEEP_EVERY = timedelta(minutes=5)
_last_swept: Optional[datetime] = None

# Rows outlive their window by this much before sweeping, guarding against
# clock skew between workers deleting a still-live window.
_SWEEP_GRACE = timedelta(minutes=5)

# Naive UTC (see clock.py) so window arithmetic never touches a timezone.
_EPOCH = datetime(1970, 1, 1)


@dataclass(frozen=True)
class RateLimitDecision:
    """The verdict for one request. Returned as a value (not raised) since
    streaming routes must carry a refusal inside a 200 body."""

    tier: str
    refused: bool
    retry_after: int = 0  # whole seconds; the Retry-After header and SSE payload
    message: str = ""


def _limits(tier: str) -> tuple[int, int]:
    """(per-minute, per-hour) for `tier`, read from `config` at call time (not
    import time) so tests can monkeypatch and operators see live env values."""
    name = tier.upper()
    return (
        getattr(config, f"RATE_LIMIT_{name}_PER_MIN"),
        getattr(config, f"RATE_LIMIT_{name}_PER_HOUR"),
    )


def _window_start(now: datetime, seconds: int) -> datetime:
    """Floor `now` to the start of its fixed window.

    Aligned to the clock (not the caller's first request) so the counter is
    one row per (bucket, window) and the increment is a single atomic
    statement. Trade-off: the classic boundary-burst, which is why every
    tier also carries an hour window.
    """
    elapsed = int((now - _EPOCH).total_seconds())
    return _EPOCH + timedelta(seconds=elapsed - elapsed % seconds)


def _bump(session: Session, *, bucket: str, window_start: datetime, expires_at: datetime) -> int:
    """Increment one counter and return its new value, in one statement:
    `INSERT … ON CONFLICT DO UPDATE SET hits = hits + 1 RETURNING hits`. The
    increment is the read, so racing callers serialize on the row lock — no
    advisory lock or app-level mutex needed.
    """
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        # SQLite (tests, standalone demo). An unhandled dialect raises here,
        # which surfaces as the fail-open path in check_rate_limit.
        from sqlalchemy.dialects.sqlite import insert

    table = RateLimitCounter.__table__
    statement = (
        insert(table)
        .values(bucket=bucket, window_start=window_start, hits=1, expires_at=expires_at)
        .on_conflict_do_update(
            index_elements=[table.c.bucket, table.c.window_start],
            # Unqualified `table.c.hits` reads the EXISTING row (not
            # `excluded.hits`), making this an increment, not a reset to 1.
            set_={"hits": table.c.hits + 1},
        )
        .returning(table.c.hits)
    )
    hits = session.execute(statement).scalar_one()
    # Commit immediately — durable and visible to other workers before this
    # request does the work the count authorized.
    session.commit()
    return hits


def sweep_expired(session: Session, *, now: Optional[datetime] = None) -> int:
    """Delete counters whose window closed. Returns the row count.

    Every distinct caller mints a row; a closed window is meaningless, so
    this is a plain indexed range delete, not an archive.
    """
    now = now or utcnow_naive()
    result = session.execute(
        delete(RateLimitCounter).where(RateLimitCounter.expires_at < now)
    )
    session.commit()
    return result.rowcount or 0


def _maybe_sweep(session: Session, now: datetime) -> None:
    """Run the sweep at most once per `_SWEEP_EVERY` per process. Best-effort:
    a failed prune must never refuse a request that was otherwise allowed."""
    global _last_swept
    if _last_swept is not None and now - _last_swept < _SWEEP_EVERY:
        return
    # Stamped before the attempt, so a failing sweep retries on schedule,
    # not on every request.
    _last_swept = now
    try:
        sweep_expired(session, now=now)
    except Exception:  # noqa: BLE001
        logger.warning("rate limit sweep failed; counters will be pruned next interval",
                       exc_info=True)
        session.rollback()


def check_rate_limit(
    session: Session,
    *,
    identity: str,
    tier: str,
    now: Optional[datetime] = None,
) -> RateLimitDecision:
    """Count this request against `identity`'s budget for `tier` and rule on it.

    `identity` is opaque here — building it is `caller_identity`'s job. A
    limit of 0 disables that window entirely (not counted, not enforced).
    """
    now = now or utcnow_naive()
    try:
        per_minute, per_hour = _limits(tier)
        budgets = {"min": per_minute, "hr": per_hour}
        for suffix, unit, seconds in _WINDOWS:
            limit = budgets[suffix]
            if limit <= 0:
                continue
            window_start = _window_start(now, seconds)
            hits = _bump(
                session,
                bucket=f"{identity}|{tier}|{suffix}",
                window_start=window_start,
                expires_at=window_start + timedelta(seconds=seconds) + _SWEEP_GRACE,
            )
            if hits > limit:
                # Never below 1 — `Retry-After: 0` reads as "retry immediately".
                retry_after = max(
                    1,
                    math.ceil((window_start + timedelta(seconds=seconds) - now).total_seconds()),
                )
                logger.info(
                    "rate limited %s on %s (%d/%s) — retry in %ds",
                    identity, tier, limit, unit, retry_after,
                )
                return RateLimitDecision(
                    tier=tier,
                    refused=True,
                    retry_after=retry_after,
                    message=(
                        f"Rate limit reached: at most {limit} requests per {unit} "
                        f"on this endpoint. Try again in {retry_after} seconds."
                    ),
                )
        _maybe_sweep(session, now)
        return RateLimitDecision(tier=tier, refused=False)
    except Exception:  # noqa: BLE001
        # Fails OPEN (see module docstring); logs loudly since silent failure
        # here is indistinguishable from working.
        logger.exception("rate limit check failed for %s on %s — allowing the request",
                         identity, tier)
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return RateLimitDecision(tier=tier, refused=False)


def caller_identity(request: Request, db: Session, *, by_ip_only: bool = False) -> str:
    """The bucket this request counts against: `user:{id}` or `ip:{addr}`.

    Resolves the session cookie directly, not via `get_current_user` — under
    PUBLIC_DEMO=1 that resolves every anonymous visitor to the same demo
    User, which would give the whole internet one shared bucket. IP is the
    only real discriminator for anonymous traffic; this also works on
    `/auth/login`, where `get_current_user` would 401.

    `by_ip_only` is for login routes, where throttling by session cookie
    would be circular — the handshake's whole point is to get one.
    """
    if not by_ip_only:
        from services.auth import SESSION_COOKIE, resolve_session  # local: import cycle

        user = resolve_session(db, request.cookies.get(SESSION_COOKIE))
        if user is not None:
            # Exempt from quota (their money), not from this (our process).
            return f"user:{user.id}"

    from services.client_ip import client_ip  # local: keeps the import graph flat

    return f"ip:{client_ip(request)}"


def sse_rate_limit_frame(decision: RateLimitDecision) -> str:
    """The named SSE frame both LLM streams emit on a rate-limit refusal.

    Rides inside a 200 (like `quota.sse_quota_frame`) because `EventSource`
    can't read a non-200 body and just reconnects — a real 429 would look
    like a silent reconnect loop. Event name and payload keys are a
    frontend contract.
    """
    payload = {
        "code": "rate_limited",
        "message": decision.message,
        "retry_after": decision.retry_after,
    }
    return f"event: rate_limited\ndata: {json.dumps(payload)}\n\n"


def rate_limited_stream(decision: RateLimitDecision) -> StreamingResponse:
    """A complete 200 SSE response carrying only the refusal.

    Returned before any retrieval or LLM call, so the refusal doesn't spend
    what it exists to avoid spending. `[DONE]` keeps the frontend on its
    normal teardown path.
    """
    async def _stream():
        yield sse_rate_limit_frame(decision)
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def rate_limit(tier: str, *, by_ip_only: bool = False):
    """Route dependency for a non-streaming route: refuses with a real 429
    and a `Retry-After` header.

    Used as `dependencies=[Depends(rate_limit(TIER_X))]` — the route body
    never sees the verdict; it either ran or never started.
    """
    def _dependency(request: Request, db: Session = Depends(get_session)) -> RateLimitDecision:
        decision = check_rate_limit(db, identity=caller_identity(request, db, by_ip_only=by_ip_only), tier=tier)
        if decision.refused:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=decision.message,
                headers={"Retry-After": str(decision.retry_after)},
            )
        return decision

    return _dependency


def stream_rate_limit(tier: str):
    """Route dependency for an SSE route: returns the verdict instead of
    raising, since a refusal here must ride inside a 200 body. Callers check
    `.refused` and return `rate_limited_stream(...)`.
    """
    def _dependency(request: Request, db: Session = Depends(get_session)) -> RateLimitDecision:
        return check_rate_limit(db, identity=caller_identity(request, db), tier=tier)

    return _dependency
