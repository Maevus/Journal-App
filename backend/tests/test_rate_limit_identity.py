"""Which bucket a request counts against.

The whole limiter rests on this one string. `PUBLIC_DEMO=1` makes it subtle:
`get_current_user` resolves every signed-out visitor to the same seeded demo
`User` row, so a limiter keyed on that id would give the whole internet one
shared bucket and let one abuser rate-limit everyone else out. `caller_identity`
resolves the session cookie itself and falls back to IP — the only discriminator
anonymous traffic has.
"""
import pytest
from sqlmodel import Session
from starlette.requests import Request

import config
from models.user import User
from services.auth import DEMO_EMAIL, DEMO_GOOGLE_SUB, SESSION_COOKIE, create_session
from services.rate_limit import caller_identity


def _request(peer="198.51.100.9", cookie=None):
    headers = []
    if cookie is not None:
        headers.append((b"cookie", f"{SESSION_COOKIE}={cookie}".encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": headers,
            "client": (peer, 44321),
        }
    )


@pytest.fixture
def logged_in(session):
    """A real user plus a live session token, in the test DB."""
    def _make(email="a@example.com", google_sub="sub-a"):
        user = User(google_sub=google_sub, email=email)
        session.add(user)
        session.commit()
        session.refresh(user)
        return user, create_session(session, user)

    return _make


def test_authenticated_caller_is_keyed_by_user(session, logged_in):
    """A signed-in user carries their bucket between networks — switching from
    home wifi to a phone must not hand them a fresh budget."""
    user, token = logged_in()
    assert caller_identity(_request(cookie=token), session) == f"user:{user.id}"


def test_anonymous_caller_is_keyed_by_ip(session):
    assert caller_identity(_request(peer="203.0.113.4"), session) == "ip:203.0.113.4"


def test_public_demo_visitors_do_not_share_one_bucket(session, monkeypatch):
    """Both requests are anonymous and both would resolve to the same demo user,
    so identity must come from the network address — otherwise the demo's whole
    rate budget is one global pool."""
    monkeypatch.setattr(config, "PUBLIC_DEMO", True)
    demo = User(google_sub=DEMO_GOOGLE_SUB, email=DEMO_EMAIL)
    session.add(demo)
    session.commit()
    session.refresh(demo)

    first = caller_identity(_request(peer="203.0.113.4"), session)
    second = caller_identity(_request(peer="203.0.113.5"), session)

    assert first != second
    assert f"user:{demo.id}" not in (first, second)


def test_an_expired_or_unknown_cookie_falls_back_to_ip(session):
    """A cookie that doesn't resolve is not an identity — otherwise "send a
    random session cookie" would mint fresh buckets."""
    identity = caller_identity(_request(peer="203.0.113.4", cookie="not-a-real-token"), session)
    assert identity == "ip:203.0.113.4"


def test_by_ip_only_ignores_even_a_valid_session(session, logged_in):
    """Keying the login handshake on a session cookie would be circular — the
    point of the handshake is to obtain one — so /auth/login and /auth/callback
    throttle per IP regardless of what the caller presents.
    """
    _, token = logged_in()
    identity = caller_identity(_request(peer="203.0.113.4", cookie=token), session, by_ip_only=True)
    assert identity == "ip:203.0.113.4"


def test_identity_honours_the_trust_boundary(session, monkeypatch):
    """`caller_identity` reads client_ip.py rather than request.client itself, so
    the forged-XFF defence applies here too — one place decides who the caller is."""
    monkeypatch.setattr(config, "TRUSTED_PROXY_IPS", "")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"x-forwarded-for", b"1.2.3.4")],
            "client": ("198.51.100.9", 1),
        }
    )
    assert caller_identity(request, session) == "ip:198.51.100.9"


def test_byo_key_holders_are_still_keyed_and_limited(session, logged_in):
    """A BYO-key user is exempt from the quota gate (their money, their key) but
    deliberately NOT from this: a rate limit protects our process, connection
    pool, and provider concurrency — none of which they paid for.
    """
    from models.keys import UserApiKey

    user, token = logged_in()
    session.add(UserApiKey(user_id=user.id, provider="anthropic", encrypted_key=b"x"))
    session.commit()

    assert caller_identity(_request(cookie=token), session) == f"user:{user.id}"


def test_identities_are_namespaced_so_a_user_id_cannot_collide_with_an_ip(session, logged_in):
    """`user:` / `ip:` prefixes, not bare values: without them a caller from
    address "7" would share a bucket with user 7 — a silent cross-tenant counter."""
    user, token = logged_in()
    assert caller_identity(_request(cookie=token), session).startswith("user:")
    assert caller_identity(_request(), session).startswith("ip:")


def test_a_valid_session_wins_over_the_demo_fallback(session, logged_in, monkeypatch):
    """Even under PUBLIC_DEMO, a signed-in visitor is themselves — matching
    get_current_user's resolution order, so limiter and auth never disagree
    about who is making a request."""
    monkeypatch.setattr(config, "PUBLIC_DEMO", True)
    session.add(User(google_sub=DEMO_GOOGLE_SUB, email=DEMO_EMAIL))
    session.commit()
    user, token = logged_in()

    assert caller_identity(_request(cookie=token), session) == f"user:{user.id}"


def test_identity_reads_from_the_session_passed_in(session, logged_in):
    """The cookie is resolved against the request's own DB session, not a
    module-level engine, so the TestClient's overridden session (and any future
    per-tenant routing) is what's consulted."""
    user, token = logged_in()
    with Session(session.get_bind()) as other:
        assert caller_identity(_request(cookie=token), other) == f"user:{user.id}"
