"""Session-based authentication — the httpOnly-cookie login (ADR 0003).

After Google login we mint a random session token, hand the *raw* token to the
browser in an httpOnly cookie, and store only its **hash** server-side. So a
stray read of the `sessions` table can't reveal a live session (same reasoning
as password hashing) — but SHA-256 (a fast hash) is the right tool here: the
token is 256 bits of randomness from `secrets`, not a guessable secret, so there
is no brute-force surface and no need for a slow KDF like bcrypt.

`get_current_user` resolves the cookie to a `User` and is the auth gate (401 if
the cookie is absent, unknown, or expired). It will become the entry point of
the isolation layer — every protected endpoint depends on it.
"""
import hashlib
import secrets
from datetime import timedelta
from typing import Optional

from fastapi import Cookie, Depends, HTTPException, status
from sqlmodel import Session, select

import config
from clock import utcnow_naive
from models.journal import get_session
from models.user import User, UserSession

SESSION_COOKIE = "session"
SESSION_TTL = timedelta(days=14)

# The demo deployment's always-logged-in account (decisions.md 2026-07-02).
# Demo mode never 401s: get_current_user resolves to this user instead, so the
# showcase stays zero-friction while running the identical isolation code path
# (everything still scoped to a real user_id — no auth bypass to fork the code).
DEMO_GOOGLE_SUB = "demo"
DEMO_EMAIL = "demo@journal.local"


def get_or_create_user(db: Session, *, google_sub: str, email: str) -> User:
    """Resolve a Google login to our account, creating it on first sight.

    The OAuth callback runs this on every login. `google_sub` (Google's stable
    identity) is the anchor: one sub → one account, forever, so a repeat login
    never mints a duplicate. `email` is a mutable display attribute, so it's
    refreshed to Google's current value. Both params are keyword-only — they're
    both strings and trivial to transpose positionally, which would mis-key an
    account; naming them at the call site removes that whole class of bug.
    """
    user = db.exec(select(User).where(User.google_sub == google_sub)).first()
    if user is None:
        user = User(google_sub=google_sub, email=email)
    elif user.email == email:
        return user  # unchanged — no write
    else:
        user.email = email
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def create_session(db: Session, user: User, ttl: timedelta = SESSION_TTL) -> str:
    """Mint a session for `user` and return the raw token for the cookie.

    Only the token's hash is persisted; the raw value is never stored, so the
    returned string is the sole copy — hand it straight to the Set-Cookie.
    """
    raw_token = secrets.token_urlsafe(32)
    db.add(
        UserSession(
            token_hash=_hash_token(raw_token),
            user_id=user.id,
            expires_at=utcnow_naive() + ttl,
        )
    )
    db.commit()
    return raw_token


def revoke_session(db: Session, raw_token: Optional[str]) -> None:
    """Server-side logout: delete the presented session row. Idempotent.

    Clearing the browser cookie alone would leave the token live in the DB —
    anyone holding a copy could keep using it until expiry. Deleting the row
    makes logout mean logout.
    """
    if not raw_token:
        return
    row = db.exec(
        select(UserSession).where(UserSession.token_hash == _hash_token(raw_token))
    ).first()
    if row is not None:
        db.delete(row)
        db.commit()


def resolve_session(db: Session, raw_token: Optional[str]) -> Optional[User]:
    """The raw cookie token → its `User`, or None if missing/unknown/expired."""
    if not raw_token:
        return None
    row = db.exec(
        select(UserSession).where(UserSession.token_hash == _hash_token(raw_token))
    ).first()
    if row is None or row.expires_at <= utcnow_naive():
        return None
    return db.get(User, row.user_id)


def get_current_user(
    session_token: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
    db: Session = Depends(get_session),
) -> User:
    """FastAPI dependency: the logged-in `User`, or 401. The auth gate."""
    if config.IS_DEMO:
        return get_or_create_user(db, google_sub=DEMO_GOOGLE_SUB, email=DEMO_EMAIL)
    user = resolve_session(db, session_token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return user
