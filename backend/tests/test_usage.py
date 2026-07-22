"""GET /usage — the per-user spend widget (freemium F5).

The widget must report exactly what the quota gate measures, so these tests
seed the *meter* DB (the one check_quota/_log_usage share — `meter_db`), not the
route session's DB. A key-holder is uncapped; everyone else sees tier budget +
remaining. Spend is scoped to the asking user and to the UTC-day window.
"""
from datetime import timedelta

import pytest
from sqlmodel import Session

import config
from clock import utcnow_naive
from models.keys import UserApiKey
from models.usage import LLMUsage


def _seed_usage(engine, *, user_id, cost, when=None, model="claude-opus-4-8"):
    """Insert one metered call into the meter DB (what the gate/widget read)."""
    with Session(engine) as s:
        s.add(
            LLMUsage(
                user_id=user_id,
                model=model,
                input_tokens=10,
                output_tokens=5,
                cost=cost,
                created_at=when or utcnow_naive(),
            )
        )
        s.commit()


def test_usage_requires_auth(client):
    assert client.get("/usage").status_code == 401


def test_usage_free_tier_no_spend(client, me, meter_db):
    body = client.get("/usage").json()
    assert body["on_own_key"] is False
    assert body["model"] == config.FREE_TIER_MODEL  # haiku
    assert body["spent_today_usd"] == 0.0
    assert body["calls_today"] == 0
    # quota_headroom (autouse) sets FREE_TIER_DAILY_USD to 100 in tests
    assert body["budget_usd"] == config.FREE_TIER_DAILY_USD
    assert body["remaining_usd"] == config.FREE_TIER_DAILY_USD
    assert len(body["daily"]) == 30
    assert all(d["spent_usd"] == 0.0 and d["calls"] == 0 for d in body["daily"])


def test_usage_sums_todays_spend_and_remaining(client, me, meter_db):
    _seed_usage(meter_db, user_id=me.id, cost=0.01)
    _seed_usage(meter_db, user_id=me.id, cost=0.02)
    # three days ago — belongs in the daily breakdown, not in "today"
    _seed_usage(meter_db, user_id=me.id, cost=0.05, when=utcnow_naive() - timedelta(days=3))

    body = client.get("/usage").json()
    assert body["spent_today_usd"] == pytest.approx(0.03)
    assert body["calls_today"] == 2
    assert body["remaining_usd"] == pytest.approx(config.FREE_TIER_DAILY_USD - 0.03)

    by_date = {d["date"]: d for d in body["daily"]}
    today_iso = utcnow_naive().date().isoformat()
    old_iso = (utcnow_naive() - timedelta(days=3)).date().isoformat()
    assert by_date[today_iso]["spent_usd"] == pytest.approx(0.03)
    assert by_date[today_iso]["calls"] == 2
    assert by_date[old_iso]["spent_usd"] == pytest.approx(0.05)
    assert by_date[old_iso]["calls"] == 1


def test_usage_excludes_other_users(client, me, meter_db):
    _seed_usage(meter_db, user_id=me.id, cost=0.01)
    _seed_usage(meter_db, user_id=me.id + 999, cost=5.0)  # a different user's spend
    body = client.get("/usage").json()
    assert body["spent_today_usd"] == pytest.approx(0.01)
    assert body["calls_today"] == 1


def test_usage_byo_user_is_uncapped(client, me, meter_db):
    with Session(meter_db) as s:
        s.add(UserApiKey(user_id=me.id, provider="anthropic", encrypted_key=b"ciphertext"))
        s.commit()
    _seed_usage(meter_db, user_id=me.id, cost=0.50)  # own-key spend is still metered

    body = client.get("/usage").json()
    assert body["on_own_key"] is True
    assert body["model"] == config.BYO_KEY_MODEL  # opus — pays their own way
    assert body["budget_usd"] is None
    assert body["remaining_usd"] is None
    assert body["spent_today_usd"] == pytest.approx(0.50)  # shown, just gates nobody


def test_usage_daily_window_respects_days_param(client, me, meter_db):
    body = client.get("/usage?days=7").json()
    assert len(body["daily"]) == 7
    # oldest first, newest (today) last
    assert body["daily"][-1]["date"] == utcnow_naive().date().isoformat()
