"""Session-based authentication — the httpOnly-cookie login (ADR 0003).

The browser holds the raw session token (httpOnly cookie); only its SHA-256
hash is stored server-side, so a DB read can't reveal a live session. SHA-256
(not bcrypt) is fine here — the token is 256 bits of `secrets` randomness, not
a guessable secret, so there's no brute-force surface to slow down.

`get_current_user` resolves the cookie to a `User` — the auth gate (401 if the
cookie is absent, unknown, or expired).
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
# get_current_user resolves to this instead of 401ing — same isolation code
# path as a real user, no auth-bypass fork.
DEMO_GOOGLE_SUB = "demo"
DEMO_EMAIL = "demo@journal.local"

# The demo user's id, cached once resolved (only a non-None result is cached,
# so this starts working as soon as the demo is seeded). Demo-ness is per-user —
# quota/model decisions ask "is this the demo user?" rather than a global flag.
_demo_user_id: Optional[int] = None


def is_demo_user(user: User) -> bool:
    """True when `user` is the seeded demo account (anchored on its stable sub)."""
    return user.google_sub == DEMO_GOOGLE_SUB


def demo_user_id(db: Session) -> Optional[int]:
    """The seeded demo user's id, or None if the demo hasn't been seeded. Cached."""
    global _demo_user_id
    if _demo_user_id is None:
        user = db.exec(select(User).where(User.google_sub == DEMO_GOOGLE_SUB)).first()
        _demo_user_id = user.id if user is not None else None
    return _demo_user_id


def is_demo_user_id(db: Session, user_id: Optional[int]) -> bool:
    """`is_demo_user` for call sites that hold only a user_id (quota, llm_client)."""
    return user_id is not None and user_id == demo_user_id(db)


def get_or_create_user(db: Session, *, google_sub: str, email: str) -> User:
    """Resolve a Google login to our account, creating it on first sight.

    `google_sub` (Google's stable id) is the anchor — one sub → one account,
    never a duplicate. `email` is refreshed to Google's current value. Both
    args are keyword-only so two adjacent strings can't be transposed and
    mis-key an account.
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

    Clearing the cookie alone would leave the token live in the DB; deleting
    the row makes logout mean logout.
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
    """FastAPI dependency: the current `User`, or 401. The auth gate.

    Order: a valid session wins. Else the seeded demo user if `PUBLIC_DEMO`
    (signed-out visitors see the read-only demo). Else 401.
    """
    user = resolve_session(db, session_token)
    if user is not None:
        return user
    if config.PUBLIC_DEMO:
        return get_or_create_user(db, google_sub=DEMO_GOOGLE_SUB, email=DEMO_EMAIL)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
    )


def require_writable_user(user: User = Depends(get_current_user)) -> User:
    """`get_current_user`, but 403 for the shared demo account.

    The demo journal is one account shared by every signed-out visitor, so a
    write from any of them is visible to all the others. Every mutating route
    depends on this instead of `get_current_user`, so the read-only invariant
    holds structurally rather than per-route.
    """
    if is_demo_user(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The demo journal is read-only — sign in to make changes.",
        )
    return user
