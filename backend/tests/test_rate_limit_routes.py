"""Route policy: which routes are limited, and what a refusal looks like on the wire.

Two shapes of refusal, and the difference is not cosmetic. A normal route gets a
real 429 with `Retry-After`, which an HTTP client can act on. The two SSE routes
get a named frame inside a **200**, because `EventSource` cannot read the body of
a non-200 response and reconnects by itself — a 429 there reaches the user as a
reconnect loop under a generic "network error", with the actual reason
unreadable. Same reasoning as the existing `event: quota` frame.

`test_a_refusal_carries_cors_headers` is the one pinning the middleware-vs-
dependency decision: Starlette's `add_middleware` prepends, so a limiter added
the obvious way in main.py lands OUTSIDE `CORSMiddleware` and its 429s ship with
no `Access-Control-Allow-Origin` at all — which the browser reports as an opaque
network failure rather than a rate limit. A route dependency runs inside it.
"""
import json
from collections import OrderedDict

import pytest
from sqlmodel import Session, select

import config
from models.rate_limit import RateLimitCounter


@pytest.fixture
def tight(monkeypatch):
    """Squeeze one tier down to `per_min` for a test (the autouse headroom
    fixture holds every other tier open)."""
    def _set(tier, per_min=1, per_hour=0):
        monkeypatch.setattr(config, f"RATE_LIMIT_{tier}_PER_MIN", per_min)
        monkeypatch.setattr(config, f"RATE_LIMIT_{tier}_PER_HOUR", per_hour)

    return _set


@pytest.fixture(autouse=True)
def _no_external_calls(monkeypatch):
    """Every limited route below would otherwise reach the network or the LLM on
    the requests the limiter *allows*. Stub the boundaries, not the limiter."""
    import routers.chat as chat
    import routers.ingest as ingest
    import routers.keys as keys
    import routers.synthesis as synthesis

    async def _no_tokens(*args, **kwargs):
        yield "ok"

    async def _condense(history, question, **kwargs):
        return question

    async def _range(*args, **kwargs):
        return (None, None)

    async def _noop(*args, **kwargs):
        pass

    monkeypatch.setattr(chat, "stream_text", _no_tokens)
    monkeypatch.setattr(chat, "condense_query", _condense)
    monkeypatch.setattr(chat, "parse_date_range", _range)
    monkeypatch.setattr(chat, "hybrid_search", lambda *a, **k: [])
    monkeypatch.setattr(synthesis, "stream_text", _no_tokens)
    monkeypatch.setattr(synthesis, "_CACHE", OrderedDict())
    monkeypatch.setattr(ingest, "score_mood_background", _noop)
    # /keys validates against Anthropic; APP_ENCRYPTION_KEY empty makes the
    # handler 503 before it gets there, which is fine — the limiter runs first.
    monkeypatch.setattr(keys, "key_is_valid", lambda raw: False)
    monkeypatch.setattr(config, "APP_ENCRYPTION_KEY", "")


def _post_search(client):
    return client.post("/search", json={"query": "anything", "limit": 5})


def _post_ingest(client):
    return client.post("/ingest", json={"entries": ["a note"], "source": "text"})


def _post_keys(client):
    return client.post("/keys", json={"api_key": "sk-ant-whatever"})


@pytest.mark.parametrize(
    "tier,send",
    [
        ("SEARCH", _post_search),
        ("INGEST", _post_ingest),
        ("KEYS", _post_keys),
    ],
)
def test_non_stream_routes_refuse_with_429_and_retry_after(client, me, tight, tier, send):
    """One request through, the next refused. The statuses of the allowed calls
    are deliberately not asserted — /keys 503s without an encryption key — since
    what is under test is that the limiter fires regardless of how the handler
    would have ended."""
    tight(tier, per_min=1)
    send(client)
    refused = send(client)

    assert refused.status_code == 429
    assert int(refused.headers["retry-after"]) >= 1
    assert "rate limit" in refused.json()["detail"].lower()


def test_login_is_throttled_per_ip(client, tight, monkeypatch):
    """§5 of the security checklist. The handshake is keyed on the address, not
    on a session cookie — the point of logging in is that you haven't got one."""
    import routers.auth as auth_routes

    async def _fake_exchange(request):
        return {"userinfo": {"sub": "sub-1", "email": "a@example.com"}}

    monkeypatch.setattr(auth_routes.oauth.google, "authorize_access_token", _fake_exchange)
    tight("AUTH", per_min=2)

    client.get("/auth/callback", follow_redirects=False)
    client.get("/auth/callback", follow_redirects=False)
    refused = client.get("/auth/callback", follow_redirects=False)

    assert refused.status_code == 429


def test_login_redirect_is_throttled_too(client, tight, monkeypatch):
    """/auth/login and /auth/callback share the tier: throttling only the
    callback would leave the outbound leg as a free request amplifier."""
    import routers.auth as auth_routes
    from starlette.responses import RedirectResponse

    async def _fake_redirect(request, redirect_uri, **kwargs):
        return RedirectResponse("https://accounts.google.com/o/oauth2/auth")

    monkeypatch.setattr(auth_routes.oauth.google, "authorize_redirect", _fake_redirect)
    tight("AUTH", per_min=1)

    client.get("/auth/login", follow_redirects=False)
    assert client.get("/auth/login", follow_redirects=False).status_code == 429


def test_a_refusal_carries_cors_headers(client, me, tight):
    """The reason this is a route dependency and not middleware. Without the
    `Access-Control-Allow-Origin` header the browser discards the response
    before the SPA sees the status, so a rate limit is indistinguishable from
    the API being unreachable."""
    tight("SEARCH", per_min=1)
    origin = {"Origin": config.FRONTEND_URL}
    client.post("/search", json={"query": "x"}, headers=origin)
    refused = client.post("/search", json={"query": "x"}, headers=origin)

    assert refused.status_code == 429
    assert refused.headers["access-control-allow-origin"] == config.FRONTEND_URL


def test_chat_refusal_rides_inside_a_200_stream(client, me, tight, monkeypatch):
    """And — the actual point of limiting chat — the refused request must not
    reach the LLM. Chat makes three calls per request (condense, date parse,
    the stream), so a refusal that still ran the handler would save nothing."""
    import routers.chat as chat

    calls = []

    async def _counting_condense(history, question, **kwargs):
        calls.append("condense")
        return question

    monkeypatch.setattr(chat, "condense_query", _counting_condense)
    tight("CHAT", per_min=1)
    body = {"messages": [{"role": "user", "content": "how was may?"}], "limit": 5}

    client.post("/stream/chat", json=body)
    refused = client.post("/stream/chat", json=body)

    assert refused.status_code == 200  # NOT 429 — EventSource can't read a 4xx body
    assert "event: rate_limited" in refused.text
    assert "data: [DONE]" in refused.text
    assert calls == ["condense"]  # the refused request never reached the LLM


def test_synthesis_refusal_rides_inside_a_200_stream(client, me, add_entry, tight):
    from datetime import datetime

    add_entry("a reflective entry", timestamp=datetime(2026, 5, 10))
    tight("SYNTHESIS", per_min=1)

    client.post("/stream/synthesis", json={"days": 0, "limit": 5})
    refused = client.post("/stream/synthesis", json={"days": 0, "limit": 5})

    assert refused.status_code == 200
    assert "event: rate_limited" in refused.text
    assert "data: [DONE]" in refused.text


def test_the_stream_frame_matches_the_agreed_payload(client, me, tight):
    """The wire contract the frontend parses, asserted end to end rather than
    only at the helper: event name, the three keys, and an integer retry."""
    tight("CHAT", per_min=1)
    body = {"messages": [{"role": "user", "content": "hi"}], "limit": 5}
    client.post("/stream/chat", json=body)
    text = client.post("/stream/chat", json=body).text

    frame = next(b for b in text.split("\n\n") if b.startswith("event: rate_limited"))
    payload = json.loads(frame.splitlines()[1][len("data: "):])
    assert payload["code"] == "rate_limited"
    assert isinstance(payload["retry_after"], int) and payload["retry_after"] >= 1
    assert payload["message"]


@pytest.mark.parametrize(
    "path,params",
    [
        ("/trends", {"days": 30}),
        ("/entries", {"date": "2026-05-10"}),
        ("/usage", {}),
        ("/auth/me", {}),
    ],
)
def test_cheap_reads_are_exempt(client, me, tight, path, params):
    """These are polled by the SPA and, in /trends' case, by the container
    HEALTHCHECK every 30 seconds — throttling it would make the container
    restart itself under load. Every tier is squeezed to 1 here, so a limiter
    accidentally attached to one of these would fail this test loudly."""
    for tier in ("CHAT", "SYNTHESIS", "INGEST", "KEYS", "SEARCH", "AUTH"):
        tight(tier, per_min=1)

    for _ in range(25):
        assert client.get(path, params=params).status_code == 200


def test_the_healthcheck_route_does_no_limiter_work_at_all(client, me, tight):
    """Stronger than "not refused": /trends must not even write a counter row.
    A row per healthcheck is a write every 30 seconds forever, per container."""
    tight("SEARCH", per_min=1)
    for _ in range(5):
        client.get("/trends", params={"days": 30})

    with Session(client.engine) as s:
        assert s.exec(select(RateLimitCounter)).all() == []


def test_exhausting_one_tier_leaves_the_others_open(client, me, tight):
    """Tiers are separate budgets because they have separate costs — a user who
    has used up their chat allowance can still search their own journal."""
    tight("CHAT", per_min=1)
    body = {"messages": [{"role": "user", "content": "hi"}], "limit": 5}
    client.post("/stream/chat", json=body)
    assert "event: rate_limited" in client.post("/stream/chat", json=body).text

    assert _post_search(client).status_code == 200


def test_anonymous_visitors_are_bucketed_by_address_end_to_end(client, tight, monkeypatch):
    """The PUBLIC_DEMO shape, through the real stack: two signed-out visitors
    both resolve to the shared demo user, and must still get separate budgets.
    `TRUSTED_PROXY_IPS` is set to the TestClient's own peer name so the forwarded
    header is believed — the same trust boundary a real deployment configures.
    """
    monkeypatch.setattr(config, "PUBLIC_DEMO", True)
    monkeypatch.setattr(config, "TRUSTED_PROXY_IPS", "testclient")
    tight("SEARCH", per_min=1)

    first = {"X-Forwarded-For": "203.0.113.1"}
    second = {"X-Forwarded-For": "203.0.113.2"}

    assert client.post("/search", json={"query": "x"}, headers=first).status_code == 200
    assert client.post("/search", json={"query": "x"}, headers=first).status_code == 429
    # A different visitor, same demo account — untouched by the first one's abuse.
    assert client.post("/search", json={"query": "x"}, headers=second).status_code == 200


def test_a_forged_forwarded_header_does_not_mint_fresh_budgets(client, me, tight, monkeypatch):
    """The trust boundary, end to end. With no proxy configured the header is
    ignored, so varying it per request buys nothing — which is the entire reason
    TRUSTED_PROXY_IPS exists and the Dockerfile no longer passes `*`."""
    monkeypatch.setattr(config, "TRUSTED_PROXY_IPS", "")
    # Signed out, so the caller is keyed by address rather than by user id.
    client.cookies.clear()
    monkeypatch.setattr(config, "PUBLIC_DEMO", True)
    tight("SEARCH", per_min=2)

    statuses = [
        client.post(
            "/search", json={"query": "x"}, headers={"X-Forwarded-For": f"203.0.113.{i}"}
        ).status_code
        for i in range(5)
    ]
    assert statuses == [200, 200, 429, 429, 429]
