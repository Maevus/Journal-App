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
    revoke_session,
)

router = APIRouter()

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


@router.get("/login")
async def login(request: Request):
    """Kick off the flow: redirect the browser to Google's consent screen."""
    redirect_uri = request.url_for("callback")
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/callback")
async def callback(request: Request, db: Session = Depends(get_session)):
    """Google's return leg: code → verified identity → our session cookie."""
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError:
        # Denied consent, stale/mismatched state, … — send the user back to
        # the app with a flag it can surface, rather than a bare error page.
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
        secure=request.url.scheme == "https",  # localhost dev stays http
    )
    return response


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    """Who am I? The SPA calls this on load to decide login state."""
    return {"id": user.id, "email": user.email}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    session_token: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
    db: Session = Depends(get_session),
):
    """Revoke the session server-side, then clear the cookie."""
    revoke_session(db, session_token)
    response.delete_cookie(SESSION_COOKIE)
