"""The limiter core: fixed-window counters in the database.

These exercise `check_rate_limit` directly, with `now` injected, so window
arithmetic is pinned without sleeping. Counters live in SQL rather than a
process dict because production runs more than one uvicorn worker and
per-process counters would multiply every limit by the worker count; the
concurrency race itself is Postgres-shaped and lives in
test_rate_limit_concurrency_pg.py.

Deliberate asymmetry with the quota gate, pinned by
`test_fails_open_when_the_counter_store_breaks`: quota fails CLOSED (it protects
money not yet spent, so refusing costs nothing); the limiter fails OPEN (it
protects our own process, and refusing the whole app on a table outage is worse
than the abuse it guards against — every route it guards needs that DB anyway).
"""
from datetime import datetime, timedelta

import pytest
from sqlmodel import Session, create_engine, select

import config
from models.rate_limit import RateLimitCounter
from services.rate_limit import (
    TIER_CHAT,
    TIER_SEARCH,
    check_rate_limit,
    sse_rate_limit_frame,
    sweep_expired,
)

# A fixed instant 12s into a minute window, so "seconds left in the window" is
# an exact number rather than whatever the clock happened to be.
_T0 = datetime(2026, 8, 4, 10, 30, 12)


@pytest.fixture
def limits(monkeypatch):
    """Set one tier's two windows (autouse `rate_limit_headroom` keeps every
    other tier out of the way)."""
    def _set(tier, *, per_min, per_hour=0):
        monkeypatch.setattr(config, f"RATE_LIMIT_{tier.upper()}_PER_MIN", per_min)
        monkeypatch.setattr(config, f"RATE_LIMIT_{tier.upper()}_PER_HOUR", per_hour)

    return _set


def _attempt(session, *, identity="ip:1.2.3.4", tier=TIER_CHAT, now=_T0):
    return check_rate_limit(session, identity=identity, tier=tier, now=now)


def test_first_request_is_allowed(session, limits):
    limits(TIER_CHAT, per_min=3)
    assert _attempt(session).refused is False


def test_refuses_once_past_the_per_minute_limit(session, limits):
    """The limit counts allowed requests, so the (limit+1)th is the first
    refusal — off by one here is a whole extra LLM call per window."""
    limits(TIER_CHAT, per_min=3)
    assert [_attempt(session).refused for _ in range(5)] == [False, False, False, True, True]


def test_counters_are_per_identity(session, limits):
    """The point under PUBLIC_DEMO: two anonymous visitors resolve to the same
    demo User, so a shared bucket would let one abuser starve the other."""
    limits(TIER_CHAT, per_min=1)
    assert _attempt(session, identity="ip:1.1.1.1").refused is False
    assert _attempt(session, identity="ip:1.1.1.1").refused is True
    assert _attempt(session, identity="ip:2.2.2.2").refused is False


def test_counters_are_per_tier(session, limits):
    """Chat costs three LLM calls, search costs a local encode — separate
    budgets, so exhausting one must not close the other."""
    limits(TIER_CHAT, per_min=1)
    limits(TIER_SEARCH, per_min=1)
    assert _attempt(session, tier=TIER_CHAT).refused is False
    assert _attempt(session, tier=TIER_CHAT).refused is True
    assert _attempt(session, tier=TIER_SEARCH).refused is False


def test_the_window_rolls_over(session, limits):
    limits(TIER_CHAT, per_min=1)
    assert _attempt(session, now=_T0).refused is False
    assert _attempt(session, now=_T0 + timedelta(seconds=30)).refused is True
    # Next minute — a fresh window, so a fresh budget.
    assert _attempt(session, now=_T0 + timedelta(seconds=60)).refused is False


def test_windows_are_aligned_not_relative_to_first_use(session, limits):
    """Fixed windows, floored to the clock: two callers starting at different
    moments in the same minute share a boundary — what makes the counter a
    single row per (bucket, window) with an atomic increment."""
    limits(TIER_CHAT, per_min=1)
    _attempt(session, now=datetime(2026, 8, 4, 10, 30, 5))
    assert _attempt(session, now=datetime(2026, 8, 4, 10, 30, 59)).refused is True
    assert _attempt(session, now=datetime(2026, 8, 4, 10, 31, 0)).refused is False


def test_the_hourly_window_catches_a_paced_abuser(session, limits):
    """Why there are two windows: a caller who stays under the per-minute limit
    and waits out each boundary could otherwise sustain limit×60 requests an
    hour forever; the hour window ceilings that."""
    limits(TIER_CHAT, per_min=2, per_hour=5)
    allowed = 0
    for minute in range(10):  # well-behaved per minute, greedy per hour
        for _ in range(2):
            if not _attempt(session, now=_T0 + timedelta(minutes=minute)).refused:
                allowed += 1
    assert allowed == 5


def test_a_refused_request_does_not_consume_the_hourly_budget(session, limits):
    """Windows are checked shortest first and return on the first refusal, so a
    client stopped by the minute limit hasn't also spent an hour slot —
    otherwise a tight retry loop (the exact thing a 429 invites) would burn its
    own hourly budget while being told to wait."""
    limits(TIER_CHAT, per_min=1, per_hour=5)
    for _ in range(20):
        _attempt(session, now=_T0)  # 1 allowed, 19 refused, all in one minute

    with Session(session.get_bind()) as s:
        hourly = s.exec(
            select(RateLimitCounter).where(RateLimitCounter.bucket.like("%|hr"))
        ).one()
    assert hourly.hits == 1


def test_zero_disables_a_window(session, limits):
    """0 is the per-tier escape hatch — shipped default for tiers with no hourly
    ceiling, and the knob an operator turns when a limit misfires in production."""
    limits(TIER_CHAT, per_min=0, per_hour=0)
    assert all(not _attempt(session).refused for _ in range(50))
    with Session(session.get_bind()) as s:
        assert s.exec(select(RateLimitCounter)).all() == []  # not even counted


def test_retry_after_is_the_time_left_in_the_window(session, limits):
    """Retry-After is when to come back, not a constant: 12s into a minute
    window leaves 48."""
    limits(TIER_CHAT, per_min=1)
    _attempt(session, now=_T0)
    decision = _attempt(session, now=_T0)
    assert decision.retry_after == 48
    assert decision.refused is True


def test_retry_after_is_never_zero(session, limits):
    """`Retry-After: 0` reads as "retry immediately" — a spin loop. The last
    instant of a window still has to round up to a second."""
    limits(TIER_CHAT, per_min=1)
    end_of_window = datetime(2026, 8, 4, 10, 30, 59, 999_000)
    _attempt(session, now=end_of_window)
    assert _attempt(session, now=end_of_window).retry_after >= 1


def test_the_refusal_message_is_human_readable(session, limits):
    limits(TIER_CHAT, per_min=1)
    _attempt(session, now=_T0)
    message = _attempt(session, now=_T0).message
    assert "1" in message and "minute" in message
    assert "48" in message  # tells them when to come back


def test_sse_frame_is_the_agreed_wire_format(session, limits):
    """A contract with the frontend: the named event and three payload keys are
    what the SPA parses. Same shape as quota's `event: quota` frame, for the
    same reason — EventSource can't read a non-200 body."""
    import json

    limits(TIER_CHAT, per_min=1)
    _attempt(session, now=_T0)
    frame = sse_rate_limit_frame(_attempt(session, now=_T0))

    head, _, body = frame.partition("\n")
    assert head == "event: rate_limited"
    assert frame.endswith("\n\n")
    payload = json.loads(body[len("data: "):].strip())
    assert payload["code"] == "rate_limited"
    assert payload["retry_after"] == 48
    assert isinstance(payload["retry_after"], int)
    assert payload["message"]


def test_expired_rows_are_swept(session, limits):
    """Without a sweep, every distinct caller mints rows forever. A counter is
    dead the moment its window closes, so the sweep is a plain indexed range delete."""
    limits(TIER_CHAT, per_min=5, per_hour=50)
    _attempt(session, now=_T0)

    with Session(session.get_bind()) as s:
        assert len(s.exec(select(RateLimitCounter)).all()) == 2  # minute + hour

    # Long after both windows (and their grace) have closed.
    swept = sweep_expired(session, now=_T0 + timedelta(days=1))
    assert swept == 2
    with Session(session.get_bind()) as s:
        assert s.exec(select(RateLimitCounter)).all() == []


def test_the_sweep_spares_a_live_window(session, limits):
    limits(TIER_CHAT, per_min=5, per_hour=50)
    _attempt(session, now=_T0)
    assert sweep_expired(session, now=_T0 + timedelta(seconds=1)) == 0


def test_fails_open_when_the_counter_store_breaks(session, limits, caplog):
    """The asymmetry with services/quota.py: the gate refuses when it can't
    verify (money still unspent); the limiter admits, since refusing every
    request in the app is worse than admitting them — but logs loudly so the
    failure isn't silent."""
    limits(TIER_CHAT, per_min=1)
    broken = Session(create_engine("sqlite:////nonexistent/nowhere.db"))

    assert check_rate_limit(broken, identity="ip:1.2.3.4", tier=TIER_CHAT).refused is False
    assert "rate limit check failed" in caplog.text.lower()
