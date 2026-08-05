"""The quota gate under real Postgres concurrency.

Gated on TEST_DATABASE_URL like the other Postgres suites — skipped by default.
Production runs several uvicorn workers against one Postgres, so the per-process
`_gate_lock` protects nothing between them; `pg_advisory_xact_lock` is the real
defence, never exercised by SQLite. It fails closed, so a broken lock statement
just refuses every call rather than loosening the gate.

Also: under READ COMMITTED, concurrent transactions can each SUM a snapshot that
excludes the other's uncommitted reservation — a single INSERT..SELECT isn't
enough here, unlike on SQLite.
"""
import os
import threading

import pytest
from sqlmodel import Session, SQLModel, create_engine, func, select

import config
from models.usage import LLMUsage, QuotaReservation
from services.quota import QuotaExceededError, check_quota

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="set TEST_DATABASE_URL to a scratch Postgres DB (name must end in `_test`) "
    "to run the quota concurrency tests — they drop the meter tables on the target",
)


@pytest.fixture
def pg_meter(monkeypatch):
    """A real Postgres schema, pointed at by the quota gate.

    Full model metadata, not just the meter table: the gate also joins the key
    table (BYO spend excluded from the cap) and reads the user table for tier budget.
    """
    import services.quota as quota

    url = os.environ["TEST_DATABASE_URL"]
    assert url.rsplit("/", 1)[-1].split("?")[0].endswith("_test"), (
        "refusing to run: TEST_DATABASE_URL must name a scratch database ending in `_test` "
        "— this fixture drops every table on it"
    )
    eng = create_engine(url)
    SQLModel.metadata.drop_all(eng)
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr(quota, "engine", eng)
    yield eng
    SQLModel.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def budget(monkeypatch):
    def _set(usd, global_usd=1000.0):
        monkeypatch.setattr(config, "FREE_TIER_DAILY_USD", usd)
        monkeypatch.setattr(config, "GLOBAL_DAILY_USD", global_usd)

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


def test_concurrent_checks_serialize_on_postgres(pg_meter, budget):
    """The finding, on the engine production actually runs: 20 simultaneous
    checks against a $1 budget, each able to consume all of it, admit one."""
    budget(1.0)

    def attempt():
        try:
            check_quota(user_id=1, estimated_cost=1.0)
            return "admitted"
        except QuotaExceededError:
            return "refused"

    outcomes = _race(attempt)
    assert outcomes.count("admitted") == 1
    assert outcomes.count("refused") == 19


def test_concurrent_checks_never_overshoot_the_budget(pg_meter, budget):
    """The property under test: not "how many callers pass" but "how much they
    can commit us to" — the bound is budget + one estimate, since admission is
    decided on headroom before the call's own claim lands.

    $0.125, not $0.10: a tenth isn't exactly representable in binary, so ten of
    them can sum to just under $1 and wobble the count. Floats are the meter's
    own convention.
    """
    budget(1.0)
    estimate = 0.125

    def attempt():
        try:
            check_quota(user_id=1, estimated_cost=estimate)
            return True
        except QuotaExceededError:
            return False

    admitted = sum(_race(attempt))
    with Session(pg_meter) as s:
        reserved = s.exec(select(func.coalesce(func.sum(QuotaReservation.amount), 0.0))).one()

    assert admitted == 8  # $1.00 / $0.125, exactly
    assert reserved <= 1.0 + estimate


def test_the_advisory_lock_alone_holds_the_gate(pg_meter, budget, monkeypatch):
    """Pins the actual production guarantee.

    Every other test here races threads in ONE process, where `_gate_lock` would
    serialize them even with a no-op advisory lock — proving the statement runs,
    not that it works. Production has one `_gate_lock` per worker, serializing
    nothing between them, so neutralising it here reproduces that: the advisory
    lock alone has to hold the gate.
    """
    import contextlib

    import services.quota as quota

    budget(1.0)
    monkeypatch.setattr(quota, "_gate_lock", contextlib.nullcontext())

    def attempt():
        try:
            check_quota(user_id=1, estimated_cost=1.0)
            return "admitted"
        except QuotaExceededError:
            return "refused"

    outcomes = _race(attempt)
    assert outcomes.count("admitted") == 1


def test_the_global_cap_holds_across_concurrent_users(pg_meter, budget):
    """The kill-switch has the same race but a worse blast radius — it bounds
    our key's worst day across everyone. Twenty users, each with roomy budget,
    must still collectively stop at the cap.
    """
    budget(1000.0, global_usd=1.0)

    def attempt_as_distinct_user():
        uid = threading.get_ident() % 100_000
        try:
            check_quota(user_id=uid, estimated_cost=0.25)
            return True
        except QuotaExceededError:
            return False

    admitted = sum(_race(attempt_as_distinct_user))
    assert admitted == 4  # $1.00 cap / $0.25 each
