"""Session-based auth: the httpOnly-cookie mechanism (ADR 0003).

Security-critical — this is the gate every protected endpoint will sit behind,
so the tests actively probe the boundaries: only-the-hash-is-stored, unknown
tokens, expired sessions, and the 401 path.
"""
from datetime import timedelta

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import models.journal as journal
from models.user import User, UserSession
from services.auth import (
    SESSION_COOKIE,
    create_session,
    get_current_user,
    resolve_session,
)


def _make_user(session, sub="sub-1", email="a@example.com"):
    user = User(google_sub=sub, email=email)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


# ---- create_session / resolve_session (pure, no HTTP) ----------------------

def test_create_session_persists_only_the_hash(session):
    """The raw token goes to the browser; the DB stores only its hash, so a
    stray read of the sessions table can't reveal a live session."""
    user = _make_user(session)
    raw_token = create_session(session, user)

    row = session.exec(select(UserSession)).one()
    assert row.token_hash != raw_token  # the raw secret is never at rest
    assert row.user_id == user.id


def test_resolve_session_round_trips_to_the_user(session):
    user = _make_user(session)
    raw_token = create_session(session, user)

    resolved = resolve_session(session, raw_token)
    assert resolved is not None
    assert resolved.id == user.id


def test_resolve_session_rejects_unknown_token(session):
    _make_user(session)
    assert resolve_session(session, "never-was-issued") is None


def test_resolve_session_rejects_missing_token(session):
    assert resolve_session(session, None) is None


def test_resolve_session_rejects_expired_session(session):
    user = _make_user(session)
    raw_token = create_session(session, user, ttl=timedelta(seconds=-1))
    assert resolve_session(session, raw_token) is None


def test_tokens_are_unique_per_session(session):
    user = _make_user(session)
    assert create_session(session, user) != create_session(session, user)


# ---- get_current_user dependency (via a throwaway guarded app) -------------

@pytest.fixture
def auth_app():
    """A one-route app guarded by get_current_user, on a fresh in-memory DB.
    Exposes `.engine` so a test can seed users/sessions into the same DB."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    app = FastAPI()

    @app.get("/whoami")
    def whoami(user: User = Depends(get_current_user)):
        return {"id": user.id, "email": user.email}

    def _override_session():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[journal.get_session] = _override_session
    client = TestClient(app)
    client.engine = engine
    yield client
    engine.dispose()


def test_get_current_user_401_without_cookie(auth_app):
    assert auth_app.get("/whoami").status_code == 401


def test_get_current_user_returns_user_with_valid_cookie(auth_app):
    with Session(auth_app.engine) as s:
        user = _make_user(s, sub="sub-x", email="me@example.com")
        raw_token = create_session(s, user)

    auth_app.cookies.set(SESSION_COOKIE, raw_token)
    resp = auth_app.get("/whoami")
    assert resp.status_code == 200
    assert resp.json()["email"] == "me@example.com"
