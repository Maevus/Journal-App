"""BYO API key entry (ADR 0003, freemium F4).

A logged-in user stores their own provider key here. The key is validated
against Anthropic on save (a real auth check, not just a format guess) and then
stored encrypted by services/key_store.py. It is never echoed back in any
response.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session

import config
from models.journal import get_session
from models.user import User
from services import key_store
from services.auth import get_current_user, is_demo_user
from services.llm_client import key_is_valid

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


@router.post("")
async def set_key(
    body: KeyIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Validate a BYO key against Anthropic, then store it encrypted."""
    if is_demo_user(user):
        # The demo account is shared across every signed-out visitor — one must
        # not set a key on it, or everyone's calls would run on that key.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Key management is disabled for the demo account"
        )
    if not config.APP_ENCRYPTION_KEY:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "BYO keys are not enabled on this server (APP_ENCRYPTION_KEY unset)",
        )
    raw = body.api_key.strip()
    if not raw:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "API key must not be empty")
    # Validate before storing: a live auth check catches typos and revoked keys
    # now, rather than as a silent failure on the user's first synthesis.
    if not await key_is_valid(raw):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Anthropic rejected that API key")
    key_store.store_user_key(db, user_id=user.id, raw_key=raw)
    return {"provider": _PROVIDER, "status": "saved"}


@router.delete("")
def delete_key(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Remove the stored key — reverts the user to the free tier / app key."""
    key_store.remove_user_key(db, user_id=user.id)
    return {"provider": _PROVIDER, "status": "removed"}
