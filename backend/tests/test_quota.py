"""Quota-before-call (freemium F2, ADR 0003).

check_quota is the money gate: it runs BEFORE an LLM call and fails closed —
the deliberate asymmetry with _log_usage, which runs after and never kills the
call it's metering. These tests exercise the public interface only:
check_quota(user_id=...) either returns or raises QuotaExceededError.

Spend rows are seeded through the same in-memory meter DB the autouse
`meter_db` fixture gives llm_client, so the tests never touch journal.db.
"""
from datetime import timedelta

import pytest
from sqlmodel import Session

import config
from clock import utcnow_naive
from models.keys import UserApiKey
from models.usage import LLMUsage
from services.quota import QuotaExceededError, check_quota

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
    """Give the free tier a nonzero budget for the tests about quota *math*
    (the default $0 refuses everything before the math matters). Also pins the
    global cap — roomy unless a test is about the cap itself — so per-user
    tests can't trip it by accident."""
    def _set(usd, global_usd=100.0):
        monkeypatch.setattr(config, "FREE_TIER_DAILY_USD", usd)
        monkeypatch.setattr(config, "GLOBAL_DAILY_USD", global_usd)

    return _set


def test_zero_budget_refuses_even_with_zero_spend(meter_db, budget):
    """The launch posture: keyless users have a $0 budget (the shipped config
    default), so the gate must refuse at zero spend — spent >= budget, not >.
    A `>` comparison would wave every $0-budget user through."""
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
    """user_id=None is system work (the user-less startup-sync mood scoring):
    metered for visibility, charged to nobody's quota. It must pass even with
    a $0 per-user budget — only the global cap can stop it."""
    budget(0.0)
    _spend(meter_db, user_id=None, cost=0.10)
    check_quota(user_id=None)  # must not raise


def test_global_cap_trumps_per_user_headroom(meter_db, budget, monkeypatch):
    """The kill-switch: once everyone's spend today reaches the global cap, a
    user with personal budget left is still refused — the cap protects our key,
    not their allowance. System (None) rows count toward it; yesterday's don't."""
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
    """The seeded demo user is keyless — under the $0 free tier the whole showcase
    would refuse (chat always, synthesis on any cache miss). It gets its own small
    env-tunable budget instead. Per-user now: a real keyless user in the SAME DB
    stays on the (here $0) free tier, so the two don't share a budget."""
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
    """A BYO user spends their own money on their own key — neither the free-tier
    budget nor the global cap applies (both protect OUR key, not their wallet).
    They pass even at a $0 free tier with spend far past the global cap."""
    budget(0.0, global_usd=5.0)
    _spend(meter_db, user_id=1, cost=99.0)
    _give_key(meter_db, user_id=1)
    check_quota(user_id=1)  # must not raise


def test_byo_spend_does_not_trip_global_cap_for_others(meter_db, budget):
    """The load-bearing F4c behavior: a BYO user's own-key spend must not refuse
    everyone else. It isn't on our key, so it's excluded from the global sum —
    free-tier users and system work still pass despite a huge BYO spend."""
    budget(10.0, global_usd=5.0)
    _give_key(meter_db, user_id=2)
    _spend(meter_db, user_id=2, cost=99.0)  # BYO user, own key

    check_quota(user_id=1)     # free-tier user: not refused by BYO spend
    check_quota(user_id=None)  # system work: not refused either


def test_fails_closed_when_the_check_itself_breaks(budget, monkeypatch):
    """The asymmetry with the meter: _log_usage never kills the call it's
    metering (money already spent); the gate refuses when it can't verify
    (money not spent yet). A broken check must surface as the same
    QuotaExceededError callers already handle — not a raw DB error, and
    never a free pass."""
    import services.quota as quota
    from sqlmodel import create_engine

    budget(100.0)  # plenty of budget — only the breakage should refuse
    monkeypatch.setattr(quota, "engine", create_engine("sqlite:////nonexistent/nowhere.db"))
    with pytest.raises(QuotaExceededError):
        check_quota(user_id=1)


def test_check_quota_trusts_the_callers_key_answer(monkeypatch, meter_db):
    """The llm_client entry points read the key row once (resolve_api_key) and
    pass its presence in — the gate must not re-read it (post-F5 cleanup)."""
    import services.quota as quota

    def boom(*a, **k):
        raise AssertionError("check_quota re-read the key row")

    monkeypatch.setattr(quota.key_store, "has_user_key", boom)
    quota.check_quota(user_id=1, on_own_key=True)   # exempt — zero further reads
    quota.check_quota(user_id=1, on_own_key=False)  # spend sums only, no key read
