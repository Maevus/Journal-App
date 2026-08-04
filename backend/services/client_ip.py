"""Who is calling? The one place the app is allowed to decide.

Rate limits are only as strong as the identity they key on, and for an
anonymous caller that identity is an IP address. `X-Forwarded-For` is a request
header — anything that can open a socket can write it — so honouring it from an
untrusted peer means an attacker sends a fresh value per request, every request
lands in its own bucket, and every per-IP limit in services/rate_limit.py is
decorative. Refusing to honour it is the opposite failure: behind a load
balancer every request arrives from the balancer, so the whole internet shares
one bucket and one abuser starves everyone.

Which of those two worlds the app is in is deployment knowledge it cannot sniff,
so it comes from `config.TRUSTED_PROXY_IPS`. Unset (the default, and what
`scripts/dev.sh` runs) means "nothing is in front of me": the socket peer is the
caller and XFF is never read at all. That is why this helper is safe under both
launch shapes without a second knob.

`backend/Dockerfile` hands the same config value to uvicorn's
`--forwarded-allow-ips`, so the ASGI server and this module cannot disagree
about who is trusted. When uvicorn's ProxyHeadersMiddleware fires it has already
rewritten `scope["client"]` to the address this walk would have chosen, so the
peer we see is the real client, it is not in the trusted set, and we return it
unchanged — the two paths converge on one answer. When it does not fire (no
`--proxy-headers`, as under dev.sh) this module does the walk itself.

The walk mirrors uvicorn's `_TrustedHosts.get_trusted_client_address` on purpose:
matching an established implementation is worth more here than a marginally
different opinion about a security-relevant parse.
"""
import ipaddress
import logging
from functools import lru_cache
from typing import Optional

from starlette.requests import Request

import config

logger = logging.getLogger(__name__)

# ASGI permits a scope with no client (a unix socket, some test harnesses). A
# limiter that raised here would take down every route it guards, so unknown
# callers get a name and share a bucket — deliberately the pessimistic one.
UNKNOWN = "unknown"

_WILDCARD = "*"


class _TrustedPeers:
    """The parsed `TRUSTED_PROXY_IPS` list: plain addresses, CIDR ranges, and
    anything else kept as a literal (a unix socket path is a legitimate peer
    name that is not an IP). Built once per distinct config value — see
    `_trusted` — because parsing per request would be pure waste on a hot path.
    """

    def __init__(self, raw: str) -> None:
        self.trust_everything = raw.strip() == _WILDCARD
        self.addresses: set = set()
        self.networks: list = []
        self.literals: set[str] = set()
        if self.trust_everything:
            return
        for part in (p.strip() for p in raw.split(",")):
            if not part:
                continue
            try:
                if "/" in part:
                    self.networks.append(ipaddress.ip_network(part, strict=False))
                else:
                    self.addresses.add(ipaddress.ip_address(part))
            except ValueError:
                # Not an IP or a network. Keep it as an exact string match rather
                # than dropping it: silently ignoring a misconfigured entry would
                # turn a typo into "this proxy is untrusted", which fails in the
                # confusing direction (one shared bucket, no error anywhere).
                self.literals.add(part)

    def __bool__(self) -> bool:
        return bool(
            self.trust_everything or self.addresses or self.networks or self.literals
        )

    def __contains__(self, host: Optional[str]) -> bool:
        if self.trust_everything:
            return True
        if not host:
            return False
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return host in self.literals
        return ip in self.addresses or any(ip in net for net in self.networks)


@lru_cache(maxsize=4)
def _trusted(raw: str) -> _TrustedPeers:
    """Parse cache keyed on the raw config string, so a test that monkeypatches
    `config.TRUSTED_PROXY_IPS` takes effect immediately (a module-level constant
    built at import would not) while production still parses exactly once."""
    return _TrustedPeers(raw)


def _strip_port(host: str) -> str:
    """`1.2.3.4:5678` → `1.2.3.4`. Some proxies append the source port; without
    this the port is a free bucket reset on every request. IPv6 literals are
    bracketed (`[::1]:443`), and a bare IPv6 address is full of colons, so only
    split when there is exactly one."""
    if host.startswith("["):
        closing = host.find("]")
        return host[1:closing] if closing > 0 else host
    if host.count(":") == 1:
        return host.split(":", 1)[0]
    return host


def _forwarded_chain(request: Request) -> list[str]:
    """`X-Forwarded-For`, left to right, normalised. Each proxy *appends*, so
    the leftmost entry is whatever the original client claimed and the rightmost
    entries are the ones our own infrastructure wrote."""
    raw = request.headers.get("x-forwarded-for", "")
    return [_strip_port(part.strip()) for part in raw.split(",") if part.strip()]


def client_ip(request: Request) -> str:
    """The caller's address, as far as the app is willing to believe it.

    Returns the socket peer unless a trusted proxy is configured *and* this
    request actually came through one. Never raises: a limiter that can crash is
    worse than a limiter that occasionally over-groups.
    """
    peer = request.client.host if request.client else None
    trusted = _trusted(config.TRUSTED_PROXY_IPS)

    # No proxy configured, or this request did not arrive through one. Either
    # way the header is unverifiable and the socket address is all there is.
    if not trusted or peer not in trusted:
        return peer or UNKNOWN

    chain = _forwarded_chain(request)
    if not chain:
        return peer or UNKNOWN
    if trusted.trust_everything:
        # Nothing is untrusted, so there is no hop to discriminate on; take the
        # client's own claim. This is the "*" posture in full: it believes the
        # caller. Kept only so the knob mirrors --forwarded-allow-ips.
        return chain[0]
    for candidate in reversed(chain):
        if candidate not in trusted:
            return candidate
    # Every hop names one of our own proxies, so none of them is the client.
    # Fall back to the leftmost, matching uvicorn.
    return chain[0]
