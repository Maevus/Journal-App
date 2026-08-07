"""Google Sign-In (ADR 0003): Authorization Code flow via Authlib.

/auth/login redirects to Google with a `state` stashed in the signed
SessionMiddleware cookie (CSRF protection); /auth/callback verifies it,
exchanges the code, and checks the id_token signature. That cookie is only
handshake scaffolding — real identity lives in the `sessions` table behind
the httpOnly cookie (services/auth.py).
"""
import logging
from typing import Optional

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlmodel import Session

import config
from models.journal import get_session
from models.user import User
from services.account import delete_account
from services.auth import (
    SESSION_COOKIE,
    SESSION_TTL,
    create_session,
    get_current_user,
    get_or_create_user,
    is_demo_user,
    require_writable_user,
    revoke_session,
)
from services.rate_limit import TIER_AUTH, rate_limit

logger = logging.getLogger(__name__)

router = APIRouter()

# Both legs throttled per IP — no session exists yet to key on. Limiting only
# the callback would leave login as a free redirect amplifier and the
# callback as an unbounded guessing surface for a stolen `code` (security
# checklist §5).
_LOGIN_RATE_LIMIT = Depends(rate_limit(TIER_AUTH, by_ip_only=True))

oauth = OAuth()
oauth.register(
    "google",
    client_id=config.GOOGLE_OAUTH_CLIENT_ID,
    client_secret=config.GOOGLE_OAUTH_CLIENT_SECRET,
    # Google's discovery document supplies the authorize/token/keys endpoints.
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    # Minimal scope: `sub` + `email` is all identity needs.
    client_kwargs={"scope": "openid email"},
)


@router.get("/login", dependencies=[_LOGIN_RATE_LIMIT])
async def login(request: Request):
    """Kick off the flow: redirect the browser to Google's consent screen."""
    redirect_uri = request.url_for("callback")
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/callback", dependencies=[_LOGIN_RATE_LIMIT])
async def callback(request: Request, db: Session = Depends(get_session)):
    """Google's return leg: code → verified identity → our session cookie."""
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError as exc:
        # Consent denied / stale state / etc: redirect back with a flag instead
        # of a bare error page. Log error+description (failure mode) and
        # whether the state cookie survived, since a swallowed OAuth failure
        # is otherwise invisible.
        logger.warning(
            "OAuth callback failed: error=%r description=%r has_oauth_state_cookie=%s",
            getattr(exc, "error", None),
            getattr(exc, "description", None),
            "oauth_state" in request.cookies,
        )
        return RedirectResponse(
            f"{config.FRONTEND_URL}?auth_error=1", status_code=status.HTTP_302_FOUND
        )
    userinfo = token["userinfo"]  # id_token claims, signature already verified
    user = get_or_create_user(db, google_sub=userinfo["sub"], email=userinfo["email"])
    raw_token = create_session(db, user)
    response = RedirectResponse(config.FRONTEND_URL, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        SESSION_COOKIE,
        raw_token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,  # invisible to JS — an XSS can't exfiltrate the token
        samesite="lax",  # sent on top-level navigation, withheld cross-site
        secure=config.COOKIE_SECURE,  # explicit: request.url.scheme reads http behind a TLS proxy
    )
    return response


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    """Who am I? The SPA calls this on load to decide login state.

    `demo` flags the shared demo account (signed-out under PUBLIC_DEMO, or
    APP_MODE=demo) so the SPA hides key management and offers "sign in to
    start your own".
    """
    return {"id": user.id, "email": user.email, "demo": is_demo_user(user)}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    session_token: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
    db: Session = Depends(get_session),
):
    """Revoke the session server-side, then clear the cookie."""
    revoke_session(db, session_token)
    response.delete_cookie(SESSION_COOKIE)


# The word the body must carry. A POST with a confirmation, not a bare DELETE,
# so nothing erases an account by guessing a verb.
DELETE_CONFIRMATION = "DELETE"


class AccountDeletion(BaseModel):
    confirm: str


@router.post("/delete-account", dependencies=[Depends(rate_limit(TIER_AUTH))])
def delete_my_account(
    body: AccountDeletion,
    response: Response,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_session),
):
    """Erase the account and everything in it, then log the caller out.

    `require_writable_user` keeps the shared demo account undeletable — one
    visitor deleting it would empty the demo for everyone.
    """
    if body.confirm != DELETE_CONFIRMATION:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f'Send {{"confirm": "{DELETE_CONFIRMATION}"}} to delete the account.',
        )
    counts = delete_account(db, user)
    response.delete_cookie(SESSION_COOKIE)
    return {"status": "deleted", "deleted": counts}
