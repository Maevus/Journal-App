"""The limiter under real Postgres concurrency.

Gated on TEST_DATABASE_URL like the other Postgres suites — skipped by default.
A rate limiter is only interesting when requests arrive together: any limiter
passes fed one at a time, and the naive read-then-act shape (SELECT count,
compare, UPDATE) passes every SQLite unit test here while admitting N callers
for one slot.

Unlike services/quota.py — which reads a sum, closes its session, then decides,
needing a process lock plus `pg_advisory_xact_lock` to close that window — the
decision and the write here are the *same* statement (`INSERT … ON CONFLICT …
DO UPDATE … RETURNING hits`). No window, no lock, and nothing in-process
serializing these threads: Postgres row locking is the entire defence, and what
these tests exercise.

Postgres, not SQLite, for the same reason the quota suite needs it: SQLite
serializes writers at the file level and would hide a broken concurrent
increment behind its own coarse locking.
"""
import itertools
import os
import threading

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

import config
from conftest import require_test_database_url
from models.rate_limit import RateLimitCounter
from services.rate_limit import TIER_CHAT, check_rate_limit

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="set TEST_DATABASE_URL to a scratch Postgres DB (name must end in `_test`) "
    "to run the rate limit concurrency tests — they drop every table on the target",
)


@pytest.fixture
def pg_counters():
    """A real Postgres schema holding the counter table.

    Full model metadata, not just the one table: `caller_identity` resolves
    session cookies against the same database, so a partial schema would pass
    here and fail the moment anything realistic ran.
    """
    url = require_test_database_url()
    engine = create_engine(url)
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    yield engine
    SQLModel.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def limits(monkeypatch):
    def _set(tier, *, per_min, per_hour=0):
        monkeypatch.setattr(config, f"RATE_LIMIT_{tier.upper()}_PER_MIN", per_min)
        monkeypatch.setattr(config, f"RATE_LIMIT_{tier.upper()}_PER_HOUR", per_hour)

    return _set


def _race(fn, n=20):
    """Run `fn` on n threads that all start together, and return its results."""
    results = []
    lock = threading.Lock()
    at_the_gate = threading.Barrier(n)

    def run():
        at_the_gate.wait()
        outcome = fn()
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=run) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def _attempt_factory(engine, *, identity="ip:203.0.113.9", tier=TIER_CHAT):
    """One request's worth of limiter work, on its own connection — the shape a
    real request has, where each worker holds its own session."""
    def _attempt():
        with Session(engine) as s:
            return not check_rate_limit(s, identity=identity, tier=tier).refused

    return _attempt


def test_concurrent_requests_never_exceed_the_limit(pg_counters, limits):
    """On the engine production runs: 20 simultaneous requests against a
    5-per-minute limit admit exactly 5. A read-then-act limiter would admit all
    20 here — they'd all read 0."""
    limits(TIER_CHAT, per_min=5)

    admitted = sum(_race(_attempt_factory(pg_counters)))

    assert admitted == 5


def test_every_concurrent_request_is_counted_exactly_once(pg_counters, limits):
    """No lost updates: with the limit out of the way, 20 racing requests must
    leave the counter reading exactly 20 — a read/add/write done as separate
    steps would drop some."""
    limits(TIER_CHAT, per_min=10_000)

    _race(_attempt_factory(pg_counters))

    with Session(pg_counters) as s:
        rows = s.exec(select(RateLimitCounter)).all()
    assert [row.hits for row in rows] == [20]  # one window, every request counted


def test_the_hour_window_holds_under_concurrency_too(pg_counters, limits):
    """Both windows go through the same statement, so both must survive the race
    — otherwise the hour ceiling meant to blunt boundary bursts is exactly the
    limit that leaks under load."""
    limits(TIER_CHAT, per_min=10_000, per_hour=5)

    admitted = sum(_race(_attempt_factory(pg_counters)))

    assert admitted == 5


def test_concurrent_callers_do_not_contend_across_buckets(pg_counters, limits):
    """Four callers racing at once each get their own budget. The row lock is
    per (bucket, window), so one noisy caller must not serialize or refuse
    anyone else — the PUBLIC_DEMO property, under load."""
    limits(TIER_CHAT, per_min=2)
    attempts = [
        _attempt_factory(pg_counters, identity=f"ip:203.0.113.{i}") for i in range(4)
    ]
    # Round-robin off a shared counter, not the thread id: CPython hands out ids
    # in strides, so `get_ident() % 4` would collapse every thread onto one bucket.
    turn = itertools.count()

    def _attempt_as_one_of_four():
        return attempts[next(turn) % 4]()

    admitted = sum(_race(_attempt_as_one_of_four, n=20))

    assert admitted == 8  # 4 callers x 2 each, regardless of how the threads fell


def test_a_second_burst_in_the_same_window_is_still_refused(pg_counters, limits):
    """The counter is durable, not per-connection state: a fresh set of threads
    on fresh connections sees the first burst's count, which is what makes the
    limit hold across uvicorn workers rather than per worker."""
    limits(TIER_CHAT, per_min=5)
    _race(_attempt_factory(pg_counters))

    admitted_second_burst = sum(_race(_attempt_factory(pg_counters)))

    assert admitted_second_burst == 0
