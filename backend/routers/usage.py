"""Per-user LLM usage view (ADR 0003, freemium F5).

Read-only: spend, budget, remaining, next-call model, and a per-day
breakdown. Every number comes from services/quota.py, so the widget can
never disagree with the gate. BYO key-holders show as uncapped — their
spend is metered for visibility but gates nobody (F4).
"""
from fastapi import APIRouter, Depends

from models.user import User
from services import quota
from services.auth import get_current_user
from services.llm_client import resolve_model

router = APIRouter()


@router.get("")
def get_usage(
    days: int = 30,
    user: User = Depends(get_current_user),
):
    # Clamp: the chart wants weeks, not an unbounded zero-fill loop from a hostile ?days=.
    days = max(1, min(days, 365))
    snap = quota.usage_snapshot(user_id=user.id, days=days)
    return {
        "on_own_key": snap.on_own_key,
        "model": resolve_model(user_id=user.id),
        "spent_today_usd": snap.spent_today_usd,
        "calls_today": snap.calls_today,
        "budget_usd": snap.budget_usd,
        "remaining_usd": snap.remaining_usd,
        "daily": snap.daily,
    }
