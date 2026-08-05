"""Quota-before-call (freemium F2, ADR 0003).

check_quota runs BEFORE an LLM call and fails closed — the deliberate asymmetry
with _log_usage, which runs after and never kills the call it's metering. Tests
exercise the public interface only: it returns or raises QuotaExceededError.

Spend rows are seeded through the meter DB the autouse `meter_db` fixture gives
llm_client, so tests never touch journal.db.
"""
import threading
from datetime import timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

import config
from clock import utcnow_naive
from models.keys import UserApiKey
from models.usage import LLMUsage, QuotaReservation
from services.quota import QuotaExceededError, check_quota, release_reservation

_UTC_MIDNIGHT = utcnow_naive().replace(hour=0, minute=0, second=0, microsecond=0)


def _spend(engine, *, user_id, cost, created_at=None):
    """Seed one meter row, exactly as _log_usage would have written it."""
    with Session(engine) as s:
        row = LLMUsage(
            user_id=user_id, model="test-model", input_tokens=1, output_tokens=1, cost=cost
        )
        if created_at is not None:
            row.created_at = created_at
        s.add(row)
        s.commit()


def _give_key(engine, *, user_id):
    """Mark a user as BYO by inserting a key row. The quota gate only checks
    existence, so the ciphertext is a placeholder — no encryption needed here."""
    with Session(engine) as s:
        s.add(UserApiKey(user_id=user_id, provider="anthropic", encrypted_key=b"x"))
        s.commit()


@pytest.fixture
def budget(monkeypatch):
    """Nonzero free-tier budget for tests about quota *math* (default $0 refuses
    before the math matters); also pins a roomy global cap so per-user tests
    can't trip it by accident."""
    def _set(usd, global_usd=100.0):
        monkeypatch.setattr(config, "FREE_TIER_DAILY_USD", usd)
        monkeypatch.setattr(config, "GLOBAL_DAILY_USD", global_usd)

    return _set


def test_zero_budget_refuses_even_with_zero_spend(meter_db, budget):
    """Keyless users ship with a $0 budget, so the gate must refuse at zero
    spend: spent >= budget, not >, or every $0-budget user would pass."""
    budget(0.0)
    with pytest.raises(QuotaExceededError):
        check_quota(user_id=1)


def test_under_budget_is_allowed(meter_db, budget):
    budget(1.0)
    _spend(meter_db, user_id=1, cost=0.40)
    check_quota(user_id=1)  # must not raise


def test_exactly_at_limit_refuses(meter_db, budget):
    """Spent == budget is exhausted, not \"one more call's worth left\"."""
    budget(1.0)
    _spend(meter_db, user_id=1, cost=0.60)
    _spend(meter_db, user_id=1, cost=0.40)
    with pytest.raises(QuotaExceededError):
        check_quota(user_id=1)


def test_quota_resets_at_utc_midnight(meter_db, budget):
    """Yesterday's spend is yesterday's problem: a row one second before UTC
    midnight doesn't count, a row exactly at midnight does."""
    budget(1.0)
    _spend(meter_db, user_id=1, cost=5.0, created_at=_UTC_MIDNIGHT - timedelta(seconds=1))
    check_quota(user_id=1)  # yesterday's $5 doesn't count against today

    _spend(meter_db, user_id=1, cost=1.0, created_at=_UTC_MIDNIGHT)
    with pytest.raises(QuotaExceededError):
        check_quota(user_id=1)  # today's $1 does


def test_other_users_spend_does_not_count(meter_db, budget):
    """The meter query is user-scoped like every other read in this app."""
    budget(1.0)
    _spend(meter_db, user_id=2, cost=99.0)
    check_quota(user_id=1)  # must not raise


def test_system_work_is_exempt_from_per_user_budget(meter_db, budget):
    """user_id=None is system work (startup-sync mood scoring): metered for
    visibility but charged to nobody's quota, so only the global cap can stop it."""
    budget(0.0)
    _spend(meter_db, user_id=None, cost=0.10)
    check_quota(user_id=None)  # must not raise


def test_global_cap_trumps_per_user_headroom(meter_db, budget, monkeypatch):
    """The kill-switch: once today's total spend hits the global cap, a user with
    personal budget left is still refused — it protects our key, not their
    allowance. System (None) rows count toward it; yesterday's don't."""
    budget(10.0)
    monkeypatch.setattr(config, "GLOBAL_DAILY_USD", 5.0)
    _spend(meter_db, user_id=2, cost=99.0, created_at=_UTC_MIDNIGHT - timedelta(seconds=1))
    _spend(meter_db, user_id=2, cost=3.0)
    _spend(meter_db, user_id=None, cost=2.0)
    with pytest.raises(QuotaExceededError):
        check_quota(user_id=1)


def test_global_cap_stops_system_work_too(meter_db, monkeypatch):
    """System work skips the per-user budget, not the kill-switch."""
    monkeypatch.setattr(config, "GLOBAL_DAILY_USD", 5.0)
    _spend(meter_db, user_id=2, cost=5.0)
    with pytest.raises(QuotaExceededError):
        check_quota(user_id=None)


def test_demo_user_gets_the_demo_budget_not_the_free_tier(meter_db, monkeypatch):
    """The demo user is keyless, so under the $0 free tier the whole showcase
    would refuse; it gets its own small env-tunable budget instead. A real
    keyless user in the same DB stays on the free tier — the two don't share one."""
    from models.user import User
    from services.auth import DEMO_EMAIL, DEMO_GOOGLE_SUB

    monkeypatch.setattr(config, "DEMO_DAILY_USD", 1.0)
    monkeypatch.setattr(config, "FREE_TIER_DAILY_USD", 0.0)
    monkeypatch.setattr(config, "GLOBAL_DAILY_USD", 100.0)

    with Session(meter_db) as s:
        demo = User(google_sub=DEMO_GOOGLE_SUB, email=DEMO_EMAIL)
        real = User(google_sub="real-sub", email="real@example.com")
        s.add_all([demo, real])
        s.commit()
        s.refresh(demo)
        s.refresh(real)
        demo_id, real_id = demo.id, real.id

    _spend(meter_db, user_id=demo_id, cost=0.50)
    check_quota(user_id=demo_id)  # $0.50 < the $1 demo budget → allowed

    _spend(meter_db, user_id=demo_id, cost=0.50)
    with pytest.raises(QuotaExceededError):
        check_quota(user_id=demo_id)  # $1.00 == the demo budget → exhausted

    # A real keyless user in the same DB is on the $0 free tier, not the demo's.
    with pytest.raises(QuotaExceededError):
        check_quota(user_id=real_id)


def test_own_key_user_is_exempt_from_all_caps(meter_db, budget):
    """A BYO user spends their own key, so neither the free-tier budget nor the
    global cap applies (both protect OUR key, not their wallet) — even at $0
    free tier with spend past the global cap."""
    budget(0.0, global_usd=5.0)
    _spend(meter_db, user_id=1, cost=99.0)
    _give_key(meter_db, user_id=1)
    check_quota(user_id=1)  # must not raise


def test_byo_spend_does_not_trip_global_cap_for_others(meter_db, budget):
    """F4c: a BYO user's own-key spend isn't on our key, so it's excluded from
    the global sum — free-tier users and system work still pass despite it."""
    budget(10.0, global_usd=5.0)
    _give_key(meter_db, user_id=2)
    _spend(meter_db, user_id=2, cost=99.0)  # BYO user, own key

    check_quota(user_id=1)     # free-tier user: not refused by BYO spend
    check_quota(user_id=None)  # system work: not refused either


def test_fails_closed_when_the_check_itself_breaks(budget, monkeypatch):
    """The asymmetry with the meter: _log_usage never kills a call (money already
    spent); the gate refuses when it can't verify (money not spent yet). A broken
    check must surface as QuotaExceededError, not a raw DB error or a free pass."""
    import services.quota as quota
    from sqlmodel import create_engine

    budget(100.0)  # plenty of budget — only the breakage should refuse
    monkeypatch.setattr(quota, "engine", create_engine("sqlite:////nonexistent/nowhere.db"))
    with pytest.raises(QuotaExceededError):
        check_quota(user_id=1)


def test_releasing_a_reservation_returns_the_unspent_headroom(meter_db, budget):
    """A reservation is the call's worst-case cost and must not outlive the call:
    the gate commits high before tokens exist, so once the meter holds the real
    cost the difference must go back — otherwise a $1 reservation settled by a
    $0.01 call locks up the over-claimed budget until the TTL expires it.
    """
    budget(1.0)
    reservation = check_quota(user_id=1, estimated_cost=1.0)

    with pytest.raises(QuotaExceededError):
        check_quota(user_id=1, estimated_cost=1.0)  # the whole budget is claimed

    # The call cost a cent; the meter now holds the truth, so the reservation is redundant.
    _spend(meter_db, user_id=1, cost=0.01)
    release_reservation(reservation)

    check_quota(user_id=1, estimated_cost=0.50)  # must not raise: $0.99 is free


def test_a_stranded_reservation_stops_counting_after_the_ttl(meter_db, budget):
    """A worker killed mid-call never releases its claim, indistinguishable from
    a call still running. Sums ignore anything older than RESERVATION_TTL, so a
    crash costs at most that long instead of permanently locking the budget.
    """
    budget(1.0)
    check_quota(user_id=1, estimated_cost=1.0)
    with pytest.raises(QuotaExceededError):
        check_quota(user_id=1, estimated_cost=1.0)

    # The holder died. Age its claim past the TTL.
    with Session(meter_db) as s:
        claim = s.exec(select(QuotaReservation)).one()
        claim.created_at = utcnow_naive() - config.RESERVATION_TTL - timedelta(seconds=1)
        s.add(claim)
        s.commit()

    check_quota(user_id=1, estimated_cost=1.0)  # must not raise: the budget is free again


@pytest.fixture
def concurrent_meter_db(monkeypatch, tmp_path):
    """A meter DB the gate can be raced against.

    `meter_db`'s single shared connection (StaticPool on `sqlite://`) segfaults
    under 20 threads and can't reproduce real contention anyway; file-backed
    SQLite gives each thread its own connection, the shape production has.
    """
    import services.quota as quota

    eng = create_engine(f"sqlite:///{tmp_path}/meter.db")
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr(quota, "engine", eng)
    yield eng
    eng.dispose()


def test_concurrent_checks_cannot_all_pass_one_budget(concurrent_meter_db, budget):
    """The gate must reserve headroom, not just read spend that only exists once
    a call finishes — otherwise N callers arriving together all see an empty
    meter and all pass. 20 checks against a $1 budget, each claiming the whole
    $1, must admit exactly one.
    """
    budget(1.0)
    admitted = []
    at_the_gate = threading.Barrier(20)

    def attempt():
        at_the_gate.wait()  # nobody checks until everybody is ready to check
        try:
            check_quota(user_id=1, estimated_cost=1.0)
            admitted.append(1)
        except QuotaExceededError:
            pass

    threads = [threading.Thread(target=attempt) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(admitted) == 1


def test_check_quota_trusts_the_callers_key_answer(monkeypatch, meter_db):
    """The llm_client entry points read the key row once (resolve_api_key) and
    pass its presence in — the gate must not re-read it (post-F5 cleanup)."""
    import services.quota as quota

    def boom(*a, **k):
        raise AssertionError("check_quota re-read the key row")

    monkeypatch.setattr(quota.key_store, "has_user_key", boom)
    quota.check_quota(user_id=1, on_own_key=True)   # exempt — zero further reads
    quota.check_quota(user_id=1, on_own_key=False)  # spend sums only, no key read
