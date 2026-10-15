"""Egress guard proxy against real sockets: allowed target, blocked internal targets,
pinning (connects to the exact IP it checked), and raw-socket bypass attempts."""

import socket

import httpx
import pytest

from outcomeguard.verification import netpolicy
from outcomeguard.verification.netpolicy import DevAllow
from outcomeguard.verification.proxy import EgressProxy, ThreadedProxy
from tests.vhelpers import Canary


@pytest.fixture
def allowed():
    c = Canary()
    yield c
    c.close()


@pytest.fixture
def internal():
    """Stands in for an internal service (DB/API) that must stay unreachable."""
    c = Canary()
    yield c
    c.close()


@pytest.fixture
def proxy(allowed):
    p = ThreadedProxy(EgressProxy(dev=DevAllow(frozenset({("127.0.0.1", allowed.port)}))))
    with p:
        yield p


def test_allowed_destination_passes(proxy, allowed):
    r = httpx.get(f"{allowed.base}/x", proxy=proxy.url)
    assert r.status_code == 200 and allowed.hits == [("GET", "/x")]


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/",
        "http://[::1]:5432/",
        "http://0.0.0.0/",
        "http://2130706433/",
    ],
)
def test_internal_destinations_blocked(proxy, url):
    r = httpx.get(url, proxy=proxy.url, timeout=5)
    assert r.status_code == 403 and r.headers["x-outcomeguard-blocked"]


def test_non_allowlisted_local_service_blocked(proxy, internal):
    r = httpx.get(f"{internal.base}/secret", proxy=proxy.url, timeout=5)
    assert r.status_code == 403
    assert internal.hits == []


def test_connect_tunnel_to_internal_blocked(proxy, internal):
    with socket.create_connection(("127.0.0.1", proxy.port)) as s:
        s.sendall(f"CONNECT 127.0.0.1:{internal.port} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        resp = s.recv(4096)
    assert b"403" in resp.split(b"\r\n")[0]
    assert internal.hits == []


def test_https_scheme_through_plain_proxy_request_rejected(proxy):
    with socket.create_connection(("127.0.0.1", proxy.port)) as s:
        s.sendall(b"GET https://example.com/ HTTP/1.1\r\nHost: example.com\r\n\r\n")
        assert b"403" in s.recv(4096).split(b"\r\n")[0]


def test_host_header_cannot_redirect_to_internal(proxy, allowed, internal):
    """The proxy routes by the request target it checked, never by the Host header."""
    with socket.create_connection(("127.0.0.1", proxy.port)) as s:
        s.sendall(
            f"GET {allowed.base}/ok HTTP/1.1\r\nHost: 127.0.0.1:{internal.port}\r\n\r\n".encode()
        )
        s.recv(4096)
    assert internal.hits == [] and allowed.hits == [("GET", "/ok")]


def test_dns_rebinding_cannot_switch_ip_after_check(monkeypatch, allowed, internal):
    """Resolver answers differently on each call (classic rebinding). The proxy resolves
    once per connection and connects to exactly that IP; the second answer is never used."""
    answers = iter([["127.0.0.1"], ["10.0.0.1"]])
    monkeypatch.setattr(netpolicy, "resolve", lambda h, p: next(answers))
    p = ThreadedProxy(EgressProxy(dev=DevAllow(frozenset({("127.0.0.1", allowed.port)}))))
    with p:
        r = httpx.get(f"http://rebind.test:{allowed.port}/a", proxy=p.url, timeout=5)
    assert r.status_code == 200 and allowed.hits == [("GET", "/a")]


def test_decisions_are_recorded(proxy, allowed):
    httpx.get(f"{allowed.base}/", proxy=proxy.url)
    httpx.get("http://169.254.169.254/", proxy=proxy.url, timeout=5)
    actions = [(d["action"], d.get("code")) for d in proxy.proxy.stats.snapshot()]
    assert ("allow", None) in actions and ("block", "METADATA_ADDRESS") in actions
