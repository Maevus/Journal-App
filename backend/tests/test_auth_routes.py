"""/auth routes — the HTTP surface of the login flow (ADR 0003).

Authlib's Google round-trip is mocked; these test our half: verified
sub+email → get_or_create_user → session cookie → redirect, plus /auth/me
and logout (server-side revocation, not just cookie deletion).
"""
from sqlmodel import Session, select

from models.user import User, UserSession


def _fake_google_login(monkeypatch, sub="sub-1", email="a@example.com"):
    """Short-circuits the token exchange as if Google authenticated this person."""
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


def test_callback_marks_the_session_cookie_secure_when_configured(client, monkeypatch):
    """COOKIE_SECURE must reach the Set-Cookie header. Explicit config, not
    request.url.scheme, since behind a TLS-terminating proxy the app sees
    plain http and would ship the cookie without Secure."""
    import config

    monkeypatch.setattr(config, "COOKIE_SECURE", True)
    _fake_google_login(monkeypatch)

    resp = client.get("/auth/callback", follow_redirects=False)

    assert "Secure" in resp.headers["set-cookie"]


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
    assert resp.json()["demo"] is False  # real mode — key management available


def test_me_reports_demo_for_a_signed_out_public_demo_visitor(client, monkeypatch):
    """Under PUBLIC_DEMO a signed-out visitor resolves to the demo user;
    /auth/me reports demo:true so the SPA hides key management instead of
    surfacing a 403 on save."""
    import config

    monkeypatch.setattr(config, "PUBLIC_DEMO", True)
    body = client.get("/auth/me").json()  # no session cookie
    assert body["demo"] is True
    assert body["email"] == "demo@journal.local"


def test_me_reports_not_demo_for_a_logged_in_user(client, monkeypatch):
    """A real logged-in user is themselves, not the demo — even with PUBLIC_DEMO
    on, a valid session always wins."""
    import config

    monkeypatch.setattr(config, "PUBLIC_DEMO", True)
    _fake_google_login(monkeypatch, email="me@example.com")
    client.get("/auth/callback", follow_redirects=False)

    body = client.get("/auth/me").json()
    assert body["demo"] is False
    assert body["email"] == "me@example.com"


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


def test_login_cookie_survives_oauth_state_cleanup(client, monkeypatch):
    """SessionMiddleware must not clobber the login cookie: Authlib pops the
    signed OAuth-state cookie during exchange, so the middleware appends its
    own deletion Set-Cookie to our response. Same cookie name means that
    deletion lands after ours and kills the login token in the same response
    that set it. Only this test exercises that path.
    """
    import json
    from base64 import b64encode

    import itsdangerous
    from starlette.middleware.sessions import SessionMiddleware

    import config
    import main
    import routers.auth as auth_routes

    # Forge what /auth/login leaves behind: the middleware's signed cookie
    # carrying the pending state, under whatever name it's configured.
    middleware = next(
        m for m in main.app.user_middleware if m.cls is SessionMiddleware
    )
    state_cookie = middleware.kwargs.get("session_cookie", "session")
    signer = itsdangerous.TimestampSigner(str(config.SESSION_SECRET))
    payload = {"_state_google_teststate": {"data": {}}}
    forged = signer.sign(b64encode(json.dumps(payload).encode())).decode()
    client.cookies.set(state_cookie, forged)

    async def fake_exchange(request):
        # Real Authlib consumes the pending state; the simple mock doesn't.
        request.session.pop("_state_google_teststate")
        return {"userinfo": {"sub": "sub-1", "email": "a@example.com"}}

    monkeypatch.setattr(
        auth_routes.oauth.google, "authorize_access_token", fake_exchange
    )

    resp = client.get("/auth/callback", follow_redirects=False)

    # Raw headers, not the cookie jar: httpx's jar keeps only the first
    # same-named cookie, but browsers apply Set-Cookie headers in order.
    from services.auth import SESSION_COOKIE

    session_setters = [
        v
        for k, v in resp.headers.multi_items()
        if k.lower() == "set-cookie" and v.startswith(f"{SESSION_COOKIE}=")
    ]
    assert len(session_setters) == 1, session_setters
    assert not session_setters[0].startswith(f"{SESSION_COOKIE}=null")
