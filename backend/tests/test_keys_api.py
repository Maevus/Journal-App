"""GET/POST/DELETE /keys — BYO key entry, validated against Anthropic on save.

`key_is_valid` is stubbed so tests never hit the network; storage runs
through the real key_store.
"""
import pytest
from cryptography.fernet import Fernet
from sqlmodel import Session

import config
import routers.keys as keys
import services.key_store as key_store


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    """The store needs a Fernet key to encrypt; give the suite a throwaway one."""
    monkeypatch.setattr(config, "APP_ENCRYPTION_KEY", Fernet.generate_key().decode())


def _accept(monkeypatch):
    async def ok(raw_key):
        return True

    monkeypatch.setattr(keys, "key_is_valid", ok)


def _reject(monkeypatch):
    async def no(raw_key):
        return False

    monkeypatch.setattr(keys, "key_is_valid", no)


def test_valid_key_is_validated_and_stored(client, me, monkeypatch):
    _accept(monkeypatch)
    r = client.post("/keys", json={"api_key": "sk-ant-user-own"})
    assert r.status_code == 200

    # Stored, encrypted, and readable back through the store...
    with Session(client.engine) as s:
        assert key_store.get_user_key(s, user_id=me.id) == "sk-ant-user-own"
    # ...and the status endpoint the frontend reads reflects it.
    assert client.get("/keys").json()["has_key"] is True


def test_invalid_key_is_rejected_and_not_stored(client, me, monkeypatch):
    _reject(monkeypatch)
    r = client.post("/keys", json={"api_key": "sk-ant-bad"})
    assert r.status_code == 400
    assert client.get("/keys").json()["has_key"] is False


def test_response_never_echoes_the_key(client, me, monkeypatch):
    """The raw key must never come back in a response body."""
    _accept(monkeypatch)
    r = client.post("/keys", json={"api_key": "sk-ant-secret-123"})
    assert "sk-ant-secret-123" not in r.text


def test_delete_reverts_to_the_free_tier(client, me, monkeypatch):
    _accept(monkeypatch)
    client.post("/keys", json={"api_key": "sk-ant-x"})
    assert client.delete("/keys").status_code == 200
    assert client.get("/keys").json()["has_key"] is False


def test_keys_route_requires_auth(client):
    """No login → 401, like every other data route."""
    assert client.get("/keys").status_code == 401
    assert client.post("/keys", json={"api_key": "x"}).status_code == 401


def test_demo_mode_blocks_key_management(client, monkeypatch):
    """Demo logs every visitor into one shared account — a visitor must not set
    a key on it (every visitor's calls would then run on that key)."""
    monkeypatch.setattr(config, "IS_DEMO", True)
    _accept(monkeypatch)
    assert client.post("/keys", json={"api_key": "sk-ant-x"}).status_code == 403


# Every state-mutating route (valid shape, so a failure is 403, not 422). The
# demo account is shared by every signed-out visitor, so a write from one
# affects the next — require_writable_user enforces this; new mutating
# routes belong in this list too.
#
# /search and /stream/chat are absent: POSTs that write nothing, and the
# demo is meant to exercise them (bounded by DEMO_DAILY_USD). /auth/logout
# is absent — a demo visitor has no session cookie to revoke.
WRITE_ROUTES = [
    ("post", "/ingest", {"json": {"entries": ["a new entry"], "source": "text"}}),
    ("post", "/keys", {"json": {"api_key": "sk-ant-x"}}),
    ("delete", "/keys", {}),
]


@pytest.mark.parametrize("method,path,kwargs", WRITE_ROUTES, ids=[r[1] + ":" + r[0] for r in WRITE_ROUTES])
def test_demo_user_cannot_reach_any_write_route(client, monkeypatch, method, path, kwargs):
    monkeypatch.setattr(config, "IS_DEMO", True)
    _accept(monkeypatch)

    assert getattr(client, method)(path, **kwargs).status_code == 403
