"""The single network-egress policy for verification (SSRF defence).

Every outbound connection made on behalf of a submitted website — HTTPX checks, Chromium
page loads and sub-resources, Lighthouse — goes through the egress proxy, which calls
`check_destination` for each connection and connects to the *exact IP it checked*
(no second DNS lookup → no DNS rebinding). Container networking (docker-compose) adds an
independent layer: the runner has no route to anything but the proxy.

Blocked: non-http(s) schemes, credentials in URLs, ports other than 80/443, and any
address that is not globally routable — loopback, RFC 1918, CGNAT, link-local (incl.
169.254.169.254 metadata), unique-local, multicast, reserved, unspecified, documentation,
and IPv4-mapped / 6to4 / NAT64 / Teredo encodings of those. A hostname is rejected if
*any* of its addresses is blocked.

Development escape hatch: `VERIFIER_DEV_ALLOW="127.0.0.1:8020,127.0.0.1:8010"` permits
exact ip:port pairs (local fixtures and sink). Never a blanket "allow private"; refused
when APP_ENV=production.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

ALLOWED_PORTS = {80, 443}
METADATA_IPS = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("169.254.170.2"),
    ipaddress.ip_address("fd00:ec2::254"),
    ipaddress.ip_address("100.100.100.200"),
}
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


class PolicyViolation(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class DevAllow:
    pairs: frozenset[tuple[str, int]]

    @classmethod
    def from_env(cls) -> DevAllow:
        raw = os.environ.get("VERIFIER_DEV_ALLOW", "").strip()
        if not raw:
            return cls(frozenset())
        if os.environ.get("APP_ENV", "development").lower() == "production":
            raise RuntimeError("VERIFIER_DEV_ALLOW is not permitted in production")
        pairs = set()
        for item in raw.split(","):
            host, _, port = item.strip().rpartition(":")
            pairs.add((str(ipaddress.ip_address(host.strip("[]"))), int(port)))
        return cls(frozenset(pairs))

    def permits(self, ip: str, port: int) -> bool:
        return (ip, port) in self.pairs


def ip_block_reason(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """None if the address is a safe public destination, else a reason code."""
    if ip in METADATA_IPS:
        return "METADATA_ADDRESS"
    if isinstance(ip, ipaddress.IPv6Address):
        for embedded in (ip.ipv4_mapped, ip.sixtofour, ip.teredo[1] if ip.teredo else None):
            if embedded is not None:
                return ip_block_reason(embedded) or "EMBEDDED_IPV4"
        if ip in _NAT64:
            return "NAT64_ADDRESS"
    if ip.is_loopback:
        return "LOOPBACK_ADDRESS"
    if ip.is_link_local:
        return "LINK_LOCAL_ADDRESS"
    if ip.is_private:
        return "PRIVATE_ADDRESS"
    if ip.is_multicast:
        return "MULTICAST_ADDRESS"
    if ip.is_unspecified or ip.is_reserved:
        return "RESERVED_ADDRESS"
    if isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network("100.64.0.0/10"):
        return "CGNAT_ADDRESS"
    if not ip.is_global:
        return "NON_GLOBAL_ADDRESS"
    return None


def validate_url(url: str) -> tuple[str, str, int]:
    """Static checks on a URL → (scheme, host, port). No DNS."""
    if len(url) > 2048:
        raise PolicyViolation("URL_TOO_LONG")
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise PolicyViolation("UNSAFE_SCHEME", scheme or "(none)")
    if parts.username is not None or parts.password is not None:
        raise PolicyViolation("CREDENTIALS_IN_URL")
    host = parts.hostname
    if not host:
        raise PolicyViolation("NO_HOST")
    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise PolicyViolation("BAD_PORT") from exc
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise PolicyViolation("BAD_HOST", host) from exc
    return scheme, host.lower().rstrip("."), port


def resolve(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    seen: list[str] = []
    for _family, _t, _p, _c, sockaddr in infos:
        ip = str(sockaddr[0]).split("%")[0]
        if ip not in seen:
            seen.append(ip)
    return seen


def _resolver(host: str, port: int) -> list[str]:
    return resolve(host, port)  # module-level indirection so tests can patch `resolve`


def check_destination(host: str, port: int, *, dev: DevAllow | None = None, resolver=None) -> str:
    """Decide whether a connection to host:port is allowed. Returns the IP to connect to
    (the caller must connect to exactly this IP). Raises PolicyViolation otherwise."""
    dev = dev or DevAllow(frozenset())
    host = host.strip("[]").lower().rstrip(".")
    try:
        literal = ipaddress.ip_address(host)
        addrs = [str(literal)]
    except ValueError:
        try:
            addrs = (resolver or _resolver)(host, port)
        except (OSError, UnicodeError) as exc:
            raise PolicyViolation("DNS_FAILURE", host) from exc
    if not addrs:
        raise PolicyViolation("DNS_FAILURE", host)
    if all(dev.permits(a, port) for a in addrs):
        return addrs[0]
    if port not in ALLOWED_PORTS:
        raise PolicyViolation("PORT_NOT_ALLOWED", str(port))
    for a in addrs:
        reason = ip_block_reason(ipaddress.ip_address(a))
        if reason:
            raise PolicyViolation(reason, f"{host} -> {a}")
    return addrs[0]
