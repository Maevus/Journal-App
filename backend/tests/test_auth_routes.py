"""/auth routes — the HTTP surface of the login flow (ADR 0003).

The Google round-trip itself (redirect out, code→token exchange, id_token
signature check) is Authlib's code, tested upstream — it's mocked here. These
tests cover OUR half of the callback: verified `sub`+`email` →
get_or_create_user → create_session → httpOnly cookie → redirect — plus
/auth/me as the "who am I" probe and logout as *server-side* revocation
(deleting the cookie alone would leave the session live in the DB).
"""
from sqlmodel import Session, select

from models.user import User, UserSession


def _fake_google_login(monkeypatch, sub="sub-1", email="a@example.com"):
    """Short-circuit the token exchange: Authlib hands back a verified
    identity as if Google had authenticated this person."""
    import routers.auth as auth_routes

    async def fake_exchange(request):
        return {"userinfo": {"sub": sub, "email": email}}

    monkeypatch.setattr(
        auth_routes.oauth.google, "authorize_access_token", fake_exchange
    )


def test_callback_creates_user_sets_cookie_and_redirects(client, monkeypatch):
    _fake_google_login(monkeypatch)

    resp = client.get("/auth/callback", follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == "http://localhost:5173"
    set_cookie = resp.headers["set-cookie"]
    assert "session=" in set_cookie
    assert "HttpOnly" in set_cookie  # JS must never read the session token
    with Session(client.engine) as s:
        users = s.exec(select(User)).all()
        assert len(users) == 1
        assert users[0].google_sub == "sub-1"


def test_callback_repeat_login_reuses_account(client, monkeypatch):
    _fake_google_login(monkeypatch)
    client.get("/auth/callback", follow_redirects=False)
    client.get("/auth/callback", follow_redirects=False)

    with Session(client.engine) as s:
        assert len(s.exec(select(User)).all()) == 1  # no duplicate minted
        assert len(s.exec(select(UserSession)).all()) == 2  # but a session each


def test_callback_oauth_error_redirects_without_cookie(client, monkeypatch):
    """User denied consent / state mismatch → back to the app, not a 500."""
    import routers.auth as auth_routes
    from authlib.integrations.starlette_client import OAuthError

    async def failing_exchange(request):
        raise OAuthError(error="access_denied")

    monkeypatch.setattr(
        auth_routes.oauth.google, "authorize_access_token", failing_exchange
    )

    resp = client.get("/auth/callback", follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"].startswith("http://localhost:5173")
    assert "auth_error" in resp.headers["location"]
    assert "set-cookie" not in resp.headers
    with Session(client.engine) as s:
        assert s.exec(select(User)).all() == []


def test_me_returns_user_when_logged_in(client, monkeypatch):
    _fake_google_login(monkeypatch, email="me@example.com")
    client.get("/auth/callback", follow_redirects=False)  # cookie jar keeps it

    resp = client.get("/auth/me")

    assert resp.status_code == 200
    assert resp.json()["email"] == "me@example.com"


def test_me_401_without_cookie(client):
    assert client.get("/auth/me").status_code == 401


def test_logout_revokes_session_server_side(client, monkeypatch):
    _fake_google_login(monkeypatch)
    client.get("/auth/callback", follow_redirects=False)

    resp = client.post("/auth/logout")

    assert resp.status_code == 204
    assert client.get("/auth/me").status_code == 401
    with Session(client.engine) as s:
        # The row is gone, not just the cookie — a stolen token dies with logout.
        assert s.exec(select(UserSession)).all() == []


def test_logout_without_cookie_is_idempotent(client):
    assert client.post("/auth/logout").status_code == 204
