"""The trust boundary: who the app believes is calling.

X-Forwarded-For is client-writable, so honouring it from an untrusted peer
lets an attacker mint a fresh IP bucket per request; refusing it behind a
real proxy collapses every visitor onto one address. Only hops the proxies
themselves appended (the right side of the header) are trustworthy.
"""
import pytest
from starlette.requests import Request

import config
from services.client_ip import client_ip


def _request(peer="198.51.100.9", forwarded=None):
    """A minimal ASGI scope — enough for Request.client and Request.headers."""
    headers = []
    if forwarded is not None:
        headers.append((b"x-forwarded-for", forwarded.encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": headers,
            "client": (peer, 44321) if peer else None,
        }
    )


@pytest.fixture
def trust(monkeypatch):
    """Set the trusted-proxy list for one test."""
    def _set(raw):
        monkeypatch.setattr(config, "TRUSTED_PROXY_IPS", raw)

    return _set


def test_unset_trust_ignores_a_forged_forwarded_header(trust):
    """The default, and what scripts/dev.sh runs: nothing is in front of the
    app, so the socket peer is the only thing a caller can't lie about."""
    trust("")
    assert client_ip(_request(peer="198.51.100.9", forwarded="1.2.3.4")) == "198.51.100.9"


def test_untrusted_peer_cannot_speak_for_someone_else(trust):
    """A proxy is configured, but this request didn't come through it. Believing
    its header would hand every direct caller a fresh bucket per request."""
    trust("10.0.0.1")
    assert client_ip(_request(peer="198.51.100.9", forwarded="1.2.3.4")) == "198.51.100.9"


def test_trusted_proxy_supplies_the_client(trust):
    trust("10.0.0.1")
    assert client_ip(_request(peer="10.0.0.1", forwarded="203.0.113.7")) == "203.0.113.7"


def test_chain_takes_the_rightmost_untrusted_hop(trust):
    """Each proxy *appends*, so right-hand entries are ones our infrastructure
    wrote. `9.9.9.9` is client-supplied — it must not win, or the limit is
    spoofable from behind the proxy too."""
    trust("10.0.0.1,10.0.0.2")
    req = _request(peer="10.0.0.1", forwarded="9.9.9.9, 203.0.113.7, 10.0.0.2")
    assert client_ip(req) == "203.0.113.7"


def test_all_hops_trusted_falls_back_to_the_first(trust):
    """Every hop claims to be one of our proxies, so none is a real client —
    take the leftmost, matching uvicorn's ProxyHeadersMiddleware off the same
    config value."""
    trust("10.0.0.1,10.0.0.2")
    assert client_ip(_request(peer="10.0.0.1", forwarded="10.0.0.2, 10.0.0.1")) == "10.0.0.2"


def test_cidr_range_is_trusted(trust):
    """Managed hosts hand out a proxy address from a subnet, not a fixed IP."""
    trust("10.0.0.0/8")
    assert client_ip(_request(peer="10.11.12.13", forwarded="203.0.113.7")) == "203.0.113.7"


def test_port_suffixes_are_stripped(trust):
    """Some proxies append host:port. `1.2.3.4` and `1.2.3.4:5678` must be one
    bucket, not two — otherwise the port is a free limit reset."""
    trust("10.0.0.1")
    assert client_ip(_request(peer="10.0.0.1", forwarded="203.0.113.7:5678")) == "203.0.113.7"


def test_wildcard_trusts_every_peer(trust):
    """`*` mirrors uvicorn's --forwarded-allow-ips and is still accepted, but
    it means any peer can name itself — pinned here so that cost stays visible."""
    trust("*")
    assert client_ip(_request(peer="198.51.100.9", forwarded="1.2.3.4")) == "1.2.3.4"


def test_blank_forwarded_header_falls_back_to_the_peer(trust):
    trust("10.0.0.1")
    assert client_ip(_request(peer="10.0.0.1", forwarded="   ")) == "10.0.0.1"


def test_missing_socket_peer_is_not_a_crash(trust):
    """ASGI allows a scope with no client (a unix socket, some test harnesses).
    A limiter that raises here would take down every route it guards."""
    trust("")
    assert client_ip(_request(peer=None)) == "unknown"
