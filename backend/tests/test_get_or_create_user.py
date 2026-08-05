"""get_or_create_user — the upsert the OAuth callback runs on every login.

Contract: one Google `sub` maps to exactly one account, forever. Repeat login
never mints a second row; email (mutable) is refreshed to Google's latest.
"""
from sqlmodel import select

from models.user import User
from services.auth import get_or_create_user


def _all_users(session):
    return session.exec(select(User)).all()


def test_creates_user_on_first_login(session):
    user = get_or_create_user(session, google_sub="sub-1", email="a@example.com")

    assert user.id is not None
    assert user.google_sub == "sub-1"
    assert len(_all_users(session)) == 1


def test_returns_same_account_on_repeat_login(session):
    first = get_or_create_user(session, google_sub="sub-1", email="a@example.com")
    again = get_or_create_user(session, google_sub="sub-1", email="a@example.com")

    assert again.id == first.id  # same account
    assert len(_all_users(session)) == 1  # no duplicate minted


def test_refreshes_email_when_google_reports_a_new_one(session):
    first = get_or_create_user(session, google_sub="sub-1", email="old@example.com")
    updated = get_or_create_user(session, google_sub="sub-1", email="new@example.com")

    assert updated.id == first.id  # still the same account (anchored on sub)
    assert updated.email == "new@example.com"  # display attribute kept current
    assert len(_all_users(session)) == 1
