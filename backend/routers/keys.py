"""BYO API key entry (ADR 0003, freemium F4).

Validated against Anthropic on save (a real auth check), then stored
encrypted by services/key_store.py. Never echoed back in any response.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session

import config
from models.journal import get_session
from models.user import User
from services import key_store
from services.auth import get_current_user, require_writable_user
from services.llm_client import key_is_valid
from services.rate_limit import TIER_KEYS, rate_limit

router = APIRouter()

_PROVIDER = "anthropic"


class KeyIn(BaseModel):
    api_key: str


@router.get("")
def key_status(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Whether the user has a stored key. The frontend renders "key set" from
    this — it returns no key material, only the boolean."""
    return {"provider": _PROVIDER, "has_key": key_store.has_user_key(db, user_id=user.id)}


# Rate limited though it doesn't spend quota: each attempt is a live call to
# Anthropic, so unbounded it's a key-testing oracle for stolen keys.
@router.post("", dependencies=[Depends(rate_limit(TIER_KEYS))])
async def set_key(
    body: KeyIn,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_session),
):
    """Validate a BYO key against Anthropic, then store it encrypted.

    `require_writable_user` blocks setting a key on the shared demo account —
    one key would end up serving every visitor.
    """
    if not config.APP_ENCRYPTION_KEY:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "BYO keys are not enabled on this server (APP_ENCRYPTION_KEY unset)",
        )
    raw = body.api_key.strip()
    if not raw:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "API key must not be empty")
    # Validate before storing, so typos/revoked keys fail now, not on first synthesis.
    if not await key_is_valid(raw):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Anthropic rejected that API key")
    key_store.store_user_key(db, user_id=user.id, raw_key=raw)
    return {"provider": _PROVIDER, "status": "saved"}


@router.delete("")
def delete_key(
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_session),
):
    """Remove the stored key — reverts the user to the free tier / app key."""
    key_store.remove_user_key(db, user_id=user.id)
    return {"provider": _PROVIDER, "status": "removed"}
