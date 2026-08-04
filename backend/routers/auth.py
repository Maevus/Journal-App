"""Sign in with Google — the HTTP surface of the auth layer (ADR 0003).

Authorization Code flow, two round-trips through the browser, delegated to
Authlib:

  GET /auth/login    → redirect out to Google carrying our client_id and a
                       random `state`. Authlib stashes `state` in the signed
                       SessionMiddleware cookie so the callback can verify the
                       response really answers OUR request (CSRF protection).
  GET /auth/callback → Google redirects back with a one-time `code`; Authlib
                       exchanges it server-side and verifies the id_token
                       signature against Google's published keys. From there
                       our own pieces take over: get_or_create_user →
                       create_session → httpOnly cookie → redirect to the SPA.

The SessionMiddleware cookie is scaffolding for the in-flight handshake only.
Logged-in identity lives in the `sessions` table behind the httpOnly cookie
(services/auth.py) — never in a client-side session.
"""
import logging
from typing import Optional

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Cookie, Depends, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlmodel import Session

import config
from models.journal import get_session
from models.user import User
from services.auth import (
    SESSION_COOKIE,
    SESSION_TTL,
    create_session,
    get_current_user,
    get_or_create_user,
    is_demo_user,
    revoke_session,
)
from services.rate_limit import TIER_AUTH, rate_limit

logger = logging.getLogger(__name__)

router = APIRouter()

# Both legs of the handshake are throttled, per IP rather than per session — the
# whole point of logging in is that the caller hasn't got a session yet, so
# there is nothing else to key on. Throttling only the callback would leave the
# outbound leg as a free redirect amplifier, and the callback alone is the
# guessing surface for a stolen `code` (security checklist §5).
_LOGIN_RATE_LIMIT = Depends(rate_limit(TIER_AUTH, by_ip_only=True))

oauth = OAuth()
oauth.register(
    "google",
    client_id=config.GOOGLE_OAUTH_CLIENT_ID,
    client_secret=config.GOOGLE_OAUTH_CLIENT_SECRET,
    # Google's discovery document supplies the authorize/token/keys endpoints,
    # so none of them are hardcoded here.
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    # `openid email` is everything identity needs: `sub` + `email`. No wider
    # profile scope — ask for the minimum and the consent screen stays boring.
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
        # Denied consent, stale/mismatched state, … — send the user back to
        # the app with a flag it can surface, rather than a bare error page.
        # Log why: a swallowed OAuth failure is otherwise invisible. error +
        # description name the failure mode (mismatching_state / access_denied /
        # invalid_grant …); the cookie flag distinguishes "state lost" from
        # "state present but rejected".
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

    `demo` rides along so the SPA knows this is the shared demo account (a
    signed-out visitor under PUBLIC_DEMO, or anyone under APP_MODE=demo) — it hides
    key management and shows the "sign in to start your own" CTA. Now a per-user
    fact, since one instance serves both the demo visitor and real logged-in users.
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
