"""Who is calling? The one place the app is allowed to decide — the trust
boundary every per-IP rate limit in services/rate_limit.py rests on.

`X-Forwarded-For` is attacker-writable, so honoring it from an untrusted peer
lets every request mint its own bucket (every limit voided); refusing it
behind a real load balancer puts the whole internet in one bucket instead.
Which world we're in comes from `config.TRUSTED_PROXY_IPS` — unset (default)
means XFF is never read at all.

`backend/Dockerfile` hands this same value to uvicorn's
`--forwarded-allow-ips`, so the ASGI server and this module agree. The walk
mirrors uvicorn's `_TrustedHosts.get_trusted_client_address` on purpose —
matching an established implementation beats a marginally different opinion
on a security-relevant parse.
"""
import ipaddress
import logging
from functools import lru_cache
from typing import Optional

from starlette.requests import Request

import config

logger = logging.getLogger(__name__)

# A scope can lack a client (unix socket, some test harnesses). Unknown
# callers share this bucket rather than raising and taking the route down.
UNKNOWN = "unknown"

_WILDCARD = "*"


class _TrustedPeers:
    """The parsed `TRUSTED_PROXY_IPS` list: addresses, CIDR ranges, and
    anything else kept as a literal (e.g. a unix socket path). Built once
    per distinct config value — see `_trusted`.
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
                # Not an IP or network — kept as a literal rather than
                # dropped, so a typo doesn't silently become "untrusted".
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
    """Parse cache keyed on the raw config string, so monkeypatching
    `config.TRUSTED_PROXY_IPS` in tests takes effect immediately, while
    production still parses exactly once."""
    return _TrustedPeers(raw)


def _strip_port(host: str) -> str:
    """`1.2.3.4:5678` → `1.2.3.4`. Without this, an appended port is a free
    bucket reset per request. IPv6 is bracketed (`[::1]:443`) or bare (many
    colons), so only split on exactly one colon."""
    if host.startswith("["):
        closing = host.find("]")
        return host[1:closing] if closing > 0 else host
    if host.count(":") == 1:
        return host.split(":", 1)[0]
    return host


def _forwarded_chain(request: Request) -> list[str]:
    """`X-Forwarded-For`, left to right, normalised. Each proxy appends, so
    the leftmost entry is the original client's claim."""
    raw = request.headers.get("x-forwarded-for", "")
    return [_strip_port(part.strip()) for part in raw.split(",") if part.strip()]


def client_ip(request: Request) -> str:
    """The caller's address, as far as the app is willing to believe it.

    Returns the socket peer unless a trusted proxy is configured and this
    request came through one. Never raises — a limiter that crashes is worse
    than one that occasionally over-groups.
    """
    peer = request.client.host if request.client else None
    trusted = _trusted(config.TRUSTED_PROXY_IPS)

    # No proxy configured, or this request didn't arrive through one — the
    # header is unverifiable, so the socket address is all there is.
    if not trusted or peer not in trusted:
        return peer or UNKNOWN

    chain = _forwarded_chain(request)
    if not chain:
        return peer or UNKNOWN
    if trusted.trust_everything:
        # "*": nothing is untrusted, so take the client's own claim. Kept
        # only so this knob mirrors --forwarded-allow-ips.
        return chain[0]
    for candidate in reversed(chain):
        if candidate not in trusted:
            return candidate
    # Every hop names one of our own proxies, so none of them is the client.
    # Fall back to the leftmost, matching uvicorn.
    return chain[0]
