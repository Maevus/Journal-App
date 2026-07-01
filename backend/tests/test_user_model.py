"""User identity model — the account every journal row will foreign-key to.

First slice of multi-user (ADR 0003). A User is minted from a Google login and
anchored on Google's stable `sub` claim, never the mutable email; we mint our
own `id` PK so *how* someone logged in stays decoupled from *who they are*.
"""
import pytest
from sqlalchemy.exc import IntegrityError

from models.user import User


def test_user_persists_with_our_own_pk(session):
    user = User(google_sub="google-oauth2|12345", email="maevus@gmail.com")
    session.add(user)
    session.commit()
    session.refresh(user)

    assert user.id is not None  # our PK, not Google's sub
    assert user.google_sub == "google-oauth2|12345"
    assert user.email == "maevus@gmail.com"
    assert user.created_at is not None  # stamped on insert


def test_google_sub_is_unique(session):
    """Two logins with the same Google identity must not create two accounts."""
    session.add(User(google_sub="dup-sub", email="a@example.com"))
    session.commit()

    session.add(User(google_sub="dup-sub", email="b@example.com"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_email_is_not_unique(session):
    """Email is a display attribute, not a key — it can be reassigned, so it is
    deliberately NOT constrained unique (ADR 0003: anchor on sub, not email)."""
    session.add(User(google_sub="sub-1", email="shared@example.com"))
    session.add(User(google_sub="sub-2", email="shared@example.com"))
    session.commit()  # must not raise
