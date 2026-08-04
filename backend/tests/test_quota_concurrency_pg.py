"""The quota gate under real Postgres concurrency.

Gated on TEST_DATABASE_URL, like the other Postgres suites — skipped by default.
It exists because the gate's cross-process serialization is dialect-specific and
SQLite can't exercise it: production runs several uvicorn workers against one
Postgres, so `_gate_lock` (a per-process threading.Lock) protects nothing between
them and `pg_advisory_xact_lock` is the whole defence. That statement is never
executed by the SQLite suite, and getting it wrong fails in the worst direction —
check_quota fails closed, so a broken lock statement doesn't loosen the gate, it
refuses every LLM call in the app.

Postgres also has the semantics SQLite hides: under READ COMMITTED two concurrent
transactions can each SUM a snapshot that excludes the other's uncommitted
reservation, so a single INSERT..SELECT would not be enough here even though it
would be on SQLite.
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

    The whole model metadata, not just the meter: the gate joins against the
    key table (BYO spend is excluded from the global cap) and reads the user
    table to pick a tier budget.
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
    """The property the finding was really about: not "how many callers pass"
    but "how much they can commit us to". The old gate admitted all 20 for 20x
    the budget; the bound now is budget + one estimate, because a call is
    admitted on the headroom before its own claim.

    $0.125 rather than a rounder $0.10 so the arithmetic is exact: a tenth is
    not representable in binary and ten of them sum to just under $1, which
    admits an extra caller and makes the count wobble. Money summed as floats
    is the meter's existing convention, so the test works with it.
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
    """The test that actually pins the production guarantee.

    Every other test here races threads inside ONE process, where `_gate_lock`
    would serialize them even if the advisory lock did nothing at all — they
    prove the statement runs, not that it works. Production is several uvicorn
    workers, each holding its own `_gate_lock`, which therefore serializes
    nothing between them. Neutralising it reproduces exactly that: the Postgres
    advisory lock is the only thing left, and it has to be enough on its own.
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
    """The kill-switch has the same race, and a worse blast radius: it bounds
    the worst day our key can have across everyone. Twenty different users,
    each with their own roomy budget, must still collectively stop at the cap.
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
