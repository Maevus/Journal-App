"""Per-identity rate limiting: how often, as opposed to how much.

The sibling of services/quota.py, and deliberately not the same thing. Quota
bounds what a caller can make us *spend*; this bounds what a caller can make us
*do*. The two do not substitute for each other in either direction — a BYO-key
user is quota-exempt (their money, their key) but must still be limited, because
what a limit protects here is our process, our connection pool and our Anthropic
concurrency, not our wallet. Conversely a $0-budget user is already refused by
quota but can still hammer `/search`, which spends CPU on a local ONNX encode
and no money at all.

Three decisions worth knowing before changing anything here:

**Route dependency, not middleware.** Starlette's `add_middleware` prepends, so
a limiter added the natural way in main.py ends up *outside* `CORSMiddleware`
and its 429s ship with no `Access-Control-Allow-Origin` header. The browser
then reports the refusal as an opaque network failure and the SPA cannot tell a
rate limit from the server being down. A route dependency runs inside the
middleware stack, so the refusal is a normal response with normal CORS headers.
`test_rate_limit_routes.py` pins that.

**Named tiers, not numbers at call sites.** A route declares which tier it
belongs to and the budget lives in config, so the whole policy is one reviewable
table rather than a scatter of literals, and an operator can retune a limit
without a code change.

**Fails open.** The inverse of the quota gate, which fails closed because the
money it guards is not yet spent so refusing costs nothing. A limiter that
refuses everything when its table is unreachable is a bigger outage than the
abuse it prevents — and every route it guards needs that same database anyway,
so a store that cannot answer is already a total outage by other means. It logs
loudly instead.
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

# (suffix used in the bucket key, human word for the refusal message, seconds).
# Shortest first, and the check returns on the first refusal — so a caller
# stopped by the minute limit has not also spent an hour slot. Otherwise a
# client retrying in a tight loop (exactly what a 429 invites) would burn its
# own hourly budget while being told to wait.
_WINDOWS: tuple[tuple[str, str, int], ...] = (
    ("min", "minute", 60),
    ("hr", "hour", 3600),
)

# Expired rows are pruned opportunistically on the request path, at most this
# often per process. No scheduler: APScheduler is optional here and off by
# default, and a limiter that silently stops pruning when the scheduler is
# disabled is a slow disk leak. The table is small and the delete is one indexed
# range scan, so the cost lands on roughly one request every five minutes.
_SWEEP_EVERY = timedelta(minutes=5)
_last_swept: Optional[datetime] = None

# Rows outlive their window by this much before the sweep takes them. Pure
# paranoia about clock skew between workers: deleting a row a live window still
# needs would hand its owner a free reset.
_SWEEP_GRACE = timedelta(minutes=5)

# Naive UTC throughout, like every other timestamp in this app (see clock.py),
# so window arithmetic never touches a timezone or a local-time `timestamp()`.
_EPOCH = datetime(1970, 1, 1)


@dataclass(frozen=True)
class RateLimitDecision:
    """The verdict for one request. Streaming routes need the refusal as a
    value rather than an exception, because their refusal has to ride inside a
    200 response body — so the whole limiter returns one of these and only the
    non-streaming dependency turns it into a status code."""

    tier: str
    refused: bool
    retry_after: int = 0  # whole seconds; the Retry-After header and SSE payload
    message: str = ""


def _limits(tier: str) -> tuple[int, int]:
    """(per-minute, per-hour) for `tier`, read from `config` at call time rather
    than captured at import. Tests monkeypatch these knobs, and an operator
    reading them from the environment gets the value they set, not the value
    that existed when the module graph was first imported."""
    name = tier.upper()
    return (
        getattr(config, f"RATE_LIMIT_{name}_PER_MIN"),
        getattr(config, f"RATE_LIMIT_{name}_PER_HOUR"),
    )


def _window_start(now: datetime, seconds: int) -> datetime:
    """Floor `now` to the start of its fixed window.

    Aligned to the clock, not to the caller's first request: that is what makes
    the counter a single row per (bucket, window), which in turn is what makes
    the increment a single atomic statement. The cost is the classic fixed-window
    boundary burst — a caller can spend a full window's budget just before a
    boundary and another just after — which is precisely why every expensive
    tier also carries an hour window.
    """
    elapsed = int((now - _EPOCH).total_seconds())
    return _EPOCH + timedelta(seconds=elapsed - elapsed % seconds)


def _bump(session: Session, *, bucket: str, window_start: datetime, expires_at: datetime) -> int:
    """Increment one counter and return its new value, in one statement.

    `INSERT … ON CONFLICT (bucket, window_start) DO UPDATE SET hits = hits + 1
    RETURNING hits`. The increment *is* the read, so two callers racing on the
    same bucket cannot both observe the pre-increment value — the second blocks
    on the first's row lock and then sees its write. No advisory lock, no
    application-level mutex, and nothing that behaves differently across
    processes.
    """
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        # SQLite (tests, standalone demo). Any other dialect would need its own
        # upsert spelling; there isn't one, and raising here surfaces as the
        # fail-open path in check_rate_limit rather than as a wrong answer.
        from sqlalchemy.dialects.sqlite import insert

    table = RateLimitCounter.__table__
    statement = (
        insert(table)
        .values(bucket=bucket, window_start=window_start, hits=1, expires_at=expires_at)
        .on_conflict_do_update(
            index_elements=[table.c.bucket, table.c.window_start],
            # The unqualified column reads the EXISTING row's value (the proposed
            # one would be `excluded.hits`), which is what makes this an
            # increment rather than a reset to 1.
            set_={"hits": table.c.hits + 1},
        )
        .returning(table.c.hits)
    )
    hits = session.execute(statement).scalar_one()
    # Commit immediately: the count must be durable and visible to the other
    # workers before this request does any of the work the count was authorising.
    session.commit()
    return hits


def sweep_expired(session: Session, *, now: Optional[datetime] = None) -> int:
    """Delete counters whose window closed. Returns the row count.

    Every distinct caller mints rows, so without this the table grows with the
    size of the internet. A counter is meaningless the moment its window closes,
    so there is nothing to archive — this is a plain indexed range delete.
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
    # Stamped before the attempt, so a persistently failing sweep retries on the
    # interval instead of on every single request.
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

    `identity` is opaque here — building it (and getting the PUBLIC_DEMO case
    right) is `caller_identity`'s job, so this function has exactly one concern.
    A window whose limit is 0 is not enforced and not even counted: 0 is the
    per-tier off switch, and a disabled window should not still be writing rows.
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
                # Round up, and never below 1: `Retry-After: 0` reads as "retry
                # immediately", which is a spin loop dressed as politeness.
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
        # Fails OPEN — see the module docstring. Loud, because a limiter that
        # silently stops limiting is indistinguishable from one that works.
        logger.exception("rate limit check failed for %s on %s — allowing the request",
                         identity, tier)
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return RateLimitDecision(tier=tier, refused=False)


def caller_identity(request: Request, db: Session, *, by_ip_only: bool = False) -> str:
    """The bucket this request counts against: `user:{id}` or `ip:{addr}`.

    Resolves the session cookie itself rather than depending on
    `get_current_user`, and that is the load-bearing detail. Under
    `PUBLIC_DEMO=1` `get_current_user` resolves EVERY anonymous visitor to the
    same seeded demo `User` row (services/auth.py), so keying on the user id it
    returns would give the entire internet one shared bucket — one abuser would
    then rate-limit every other visitor out of the demo. For anonymous traffic
    the IP is the only discriminator there is. Resolving the cookie directly also
    means this works on `/auth/login`, where there is no user yet and
    `get_current_user` would 401.

    `by_ip_only` is for the login routes: throttling the handshake per session
    cookie would be circular, since the point of the handshake is to get one.
    """
    if not by_ip_only:
        from services.auth import SESSION_COOKIE, resolve_session  # local: import cycle

        user = resolve_session(db, request.cookies.get(SESSION_COOKIE))
        if user is not None:
            # A BYO-key holder lands here too. They are exempt from quota (their
            # money) and deliberately NOT exempt from this (our process).
            return f"user:{user.id}"

    from services.client_ip import client_ip  # local: keeps the import graph flat

    return f"ip:{client_ip(request)}"


def sse_rate_limit_frame(decision: RateLimitDecision) -> str:
    """The named SSE frame both LLM streams emit on a rate-limit refusal.

    The twin of `quota.sse_quota_frame`, for the identical reason: `EventSource`
    cannot read the body of a non-200 response and reconnects on its own, so a
    real 429 would reach the user as a silent reconnect loop under a generic
    error. The refusal therefore rides inside a 200 stream. The event name and
    the three payload keys are a contract with the frontend — this is the one
    place they are defined.
    """
    payload = {
        "code": "rate_limited",
        "message": decision.message,
        "retry_after": decision.retry_after,
    }
    return f"event: rate_limited\ndata: {json.dumps(payload)}\n\n"


def rate_limited_stream(decision: RateLimitDecision) -> StreamingResponse:
    """A complete, well-formed 200 SSE response carrying only the refusal.

    Both streaming routes return this from the top of the handler, before any
    retrieval or LLM call — the point of refusing chat is not spending the three
    LLM calls it would otherwise make. `[DONE]` is included so the frontend's
    normal stream teardown runs instead of its error path.
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
    """Route dependency for a normal (non-streaming) route: refuses with a real
    429 and a `Retry-After` header, which is what an HTTP client can act on.

    Used as `dependencies=[Depends(rate_limit(TIER_X))]` on the decorator, since
    the route body has no use for the verdict — either it ran or it never
    started.
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
    raising, because a refusal here has to be a frame inside a 200 body rather
    than a status code. The route checks `.refused` and returns
    `rate_limited_stream(...)` — see routers/chat.py and routers/synthesis.py.
    """
    def _dependency(request: Request, db: Session = Depends(get_session)) -> RateLimitDecision:
        return check_rate_limit(db, identity=caller_identity(request, db), tier=tier)

    return _dependency
