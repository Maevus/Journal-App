"""Account deletion (security checklist §2, §10): the account and everything
keyed on it goes, and nobody else's does.

Every table a user owns is seeded before each delete, so a table added later
without a matching delete shows up as a surviving row here.
"""
from datetime import datetime

import pytest
from sqlmodel import Session, select

import config
from models.journal import JournalEntry
from models.keys import UserApiKey
from models.ocr import BlankPage
from models.rate_limit import RateLimitCounter
from models.usage import LLMUsage, QuotaReservation
from models.user import User, UserSession
from services.auth import DEMO_EMAIL, DEMO_GOOGLE_SUB, SESSION_COOKIE

_CONFIRM = {"confirm": "DELETE"}


def _seed_owned_rows(engine, user_id: int, tag: str) -> None:
    """One row per user-keyed table, beyond the entries and session the
    fixtures already make."""
    with Session(engine) as s:
        s.add_all([
            UserApiKey(user_id=user_id, provider="anthropic", encrypted_key=b"ciphertext"),
            LLMUsage(user_id=user_id, model="claude", input_tokens=10, output_tokens=20, cost=0.5),
            QuotaReservation(user_id=user_id, amount=0.25),
            BlankPage(source="goodnotes", external_id=f"page-{tag}", user_id=user_id),
            RateLimitCounter(
                bucket=f"user:{user_id}|chat|min",
                window_start=datetime(2026, 8, 4, 10, 30),
                hits=3,
                expires_at=datetime(2026, 8, 4, 11, 0),
            ),
        ])
        s.commit()


def _counts(engine, user_id: int) -> dict[str, int]:
    """Rows still keyed on `user_id`, table by table."""
    with Session(engine) as s:
        def n(statement):
            return len(s.exec(statement).all())

        return {
            "journalentry": n(select(JournalEntry).where(JournalEntry.user_id == user_id)),
            "user_api_keys": n(select(UserApiKey).where(UserApiKey.user_id == user_id)),
            "llmusage": n(select(LLMUsage).where(LLMUsage.user_id == user_id)),
            "quotareservation": n(
                select(QuotaReservation).where(QuotaReservation.user_id == user_id)
            ),
            "blank_pages": n(select(BlankPage).where(BlankPage.user_id == user_id)),
            "rate_limit_counters": n(
                select(RateLimitCounter).where(
                    RateLimitCounter.bucket.startswith(f"user:{user_id}|")
                )
            ),
            "sessions": n(select(UserSession).where(UserSession.user_id == user_id)),
            "users": n(select(User).where(User.id == user_id)),
        }


def test_delete_account_empties_every_table_the_user_owns(client, login, add_entry):
    user = login()
    add_entry("a private thought", user_id=user.id)
    _seed_owned_rows(client.engine, user.id, "mine")
    assert all(c > 0 for c in _counts(client.engine, user.id).values())  # seeding worked

    resp = client.post("/auth/delete-account", json=_CONFIRM)

    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"
    remaining = _counts(client.engine, user.id)
    assert remaining == dict.fromkeys(remaining, 0)  # a surviving table names itself here


def test_delete_account_reports_what_it_deleted(client, login, add_entry):
    user = login()
    add_entry("one", user_id=user.id)
    add_entry("two", user_id=user.id)
    _seed_owned_rows(client.engine, user.id, "mine")

    deleted = client.post("/auth/delete-account", json=_CONFIRM).json()["deleted"]

    assert deleted["journalentry"] == 2
    assert deleted["users"] == 1
    assert deleted["sessions"] == 1
    assert deleted["rate_limit_counters"] >= 1


def test_delete_account_leaves_other_users_untouched(client, login, add_entry):
    """The whole point: A's wipe must not reach B's rows."""
    alice = login("alice@example.com")
    add_entry("alice private", user_id=alice.id)
    _seed_owned_rows(client.engine, alice.id, "alice")
    bob = login("bob@example.com")  # second login: the client's cookie is now bob's
    add_entry("bob note", user_id=bob.id)
    _seed_owned_rows(client.engine, bob.id, "bob")
    alice_before = _counts(client.engine, alice.id)

    assert client.post("/auth/delete-account", json=_CONFIRM).status_code == 200

    bob_remaining = _counts(client.engine, bob.id)
    assert bob_remaining == dict.fromkeys(bob_remaining, 0)
    assert _counts(client.engine, alice.id) == alice_before  # untouched, table by table


def test_delete_account_deletes_the_vector_store_rows(client, login, add_entry, monkeypatch):
    """entry_embeddings is raw SQL outside SQLModel.metadata — no ORM cascade
    reaches it, so the wipe has to call it by hand. SQLite has no such table,
    so assert on the call."""
    import services.account as account

    seen = {}

    def fake_delete(session, *, user_id):
        seen["user_id"] = user_id
        return 0

    monkeypatch.setattr(account, "delete_user_embeddings", fake_delete)
    user = login()
    add_entry("embedded", user_id=user.id)

    client.post("/auth/delete-account", json=_CONFIRM)

    assert seen["user_id"] == user.id


def test_delete_account_logs_the_caller_out(client, login, add_entry):
    user = login()
    add_entry("gone", user_id=user.id)

    resp = client.post("/auth/delete-account", json=_CONFIRM)

    assert f'{SESSION_COOKIE}=""' in resp.headers["set-cookie"]  # cookie cleared
    with Session(client.engine) as s:
        assert s.exec(select(UserSession)).all() == []  # the row is gone, not just the cookie
    assert client.get("/auth/me").status_code == 401


def test_delete_account_requires_login(client):
    assert client.post("/auth/delete-account", json=_CONFIRM).status_code == 401


@pytest.mark.parametrize(
    "body", [{"confirm": "yes"}, {"confirm": ""}, {}], ids=["wrong", "empty", "missing"]
)
def test_delete_account_requires_the_confirmation_word(client, login, add_entry, body):
    user = login()
    add_entry("still here", user_id=user.id)

    resp = client.post("/auth/delete-account", json=body)

    assert resp.status_code == 422
    assert _counts(client.engine, user.id)["journalentry"] == 1  # nothing deleted


def test_demo_account_cannot_be_deleted_over_http(client, monkeypatch, add_entry):
    """A signed-out visitor on a PUBLIC_DEMO instance resolves to the demo user.
    Without this guard they would wipe the seeded demo for everyone."""
    monkeypatch.setattr(config, "PUBLIC_DEMO", True)
    with Session(client.engine) as s:
        demo = User(google_sub=DEMO_GOOGLE_SUB, email=DEMO_EMAIL)
        s.add(demo)
        s.commit()
        s.refresh(demo)
    add_entry("seeded demo entry", user_id=demo.id)

    resp = client.post("/auth/delete-account", json=_CONFIRM)  # no cookie at all

    assert resp.status_code == 403
    assert _counts(client.engine, demo.id)["users"] == 1
    assert _counts(client.engine, demo.id)["journalentry"] == 1


def test_delete_account_service_refuses_the_demo_user(session):
    """Second guard, below the route: no script or future caller can wipe the
    demo either."""
    from services.account import delete_account

    demo = User(google_sub=DEMO_GOOGLE_SUB, email=DEMO_EMAIL)
    session.add(demo)
    session.commit()
    session.refresh(demo)

    with pytest.raises(ValueError):
        delete_account(session, demo)

    assert session.exec(select(User).where(User.id == demo.id)).first() is not None


def test_delete_account_is_rate_limited(client, login, add_entry, monkeypatch):
    """Destructive route, so it carries the same route-dependency limiter as the
    rest of /auth rather than being an unbounded hammer."""
    monkeypatch.setattr(config, "RATE_LIMIT_AUTH_PER_MIN", 1)
    monkeypatch.setattr(config, "RATE_LIMIT_AUTH_PER_HOUR", 0)
    user = login()
    add_entry("still here", user_id=user.id)

    assert client.post("/auth/delete-account", json={"confirm": "no"}).status_code == 422
    resp = client.post("/auth/delete-account", json=_CONFIRM)

    assert resp.status_code == 429
    assert resp.headers["Retry-After"]
    assert _counts(client.engine, user.id)["journalentry"] == 1  # refused before deleting
