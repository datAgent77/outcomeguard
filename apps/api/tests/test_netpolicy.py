"""SSRF policy: static URL checks, IP classification, DNS answers, dev allowlist."""

import ipaddress

import pytest

from outcomeguard.verification import netpolicy
from outcomeguard.verification.netpolicy import (
    DevAllow,
    PolicyViolation,
    check_destination,
    ip_block_reason,
    validate_url,
)


@pytest.mark.parametrize(
    "ip,reason",
    [
        ("127.0.0.1", "LOOPBACK_ADDRESS"),
        ("10.1.2.3", "PRIVATE_ADDRESS"),
        ("172.16.0.1", "PRIVATE_ADDRESS"),
        ("192.168.1.1", "PRIVATE_ADDRESS"),
        ("100.64.0.1", "CGNAT_ADDRESS"),
        ("169.254.169.254", "METADATA_ADDRESS"),
        ("169.254.10.10", "LINK_LOCAL_ADDRESS"),
        ("0.0.0.0", "PRIVATE_ADDRESS"),  # Python classifies 0.0.0.0/8 as private
        ("224.0.0.1", "MULTICAST_ADDRESS"),
        ("::1", "LOOPBACK_ADDRESS"),
        ("fd00:ec2::254", "METADATA_ADDRESS"),
        ("fc00::1", "PRIVATE_ADDRESS"),
        ("fe80::1", "LINK_LOCAL_ADDRESS"),
        ("::ffff:127.0.0.1", "LOOPBACK_ADDRESS"),
        ("::ffff:169.254.169.254", "METADATA_ADDRESS"),
        ("2002:7f00:0001::", "LOOPBACK_ADDRESS"),  # 6to4 of 127.0.0.1
        ("64:ff9b::a00:1", "NAT64_ADDRESS"),
        ("192.0.2.1", "PRIVATE_ADDRESS"),  # documentation range
    ],
)
def test_blocked_addresses(ip, reason):
    assert ip_block_reason(ipaddress.ip_address(ip)) is not None
    got = ip_block_reason(ipaddress.ip_address(ip))
    assert (
        got == reason
        or reason == "PRIVATE_ADDRESS"
        and got in {"PRIVATE_ADDRESS", "RESERVED_ADDRESS", "NON_GLOBAL_ADDRESS"}
    )


@pytest.mark.parametrize("ip", ["93.184.216.34", "1.1.1.1", "2606:4700:4700::1111"])
def test_public_addresses_allowed(ip):
    assert ip_block_reason(ipaddress.ip_address(ip)) is None


@pytest.mark.parametrize(
    "url,code",
    [
        ("file:///etc/passwd", "UNSAFE_SCHEME"),
        ("gopher://example.com/", "UNSAFE_SCHEME"),
        ("javascript:alert(1)", "UNSAFE_SCHEME"),
        ("ftp://example.com/", "UNSAFE_SCHEME"),
        ("https://user:pw@example.com/", "CREDENTIALS_IN_URL"),
        ("https://example.com:99999/", "BAD_PORT"),
        ("https:///nohost", "NO_HOST"),
    ],
)
def test_unsafe_urls_rejected(url, code):
    with pytest.raises(PolicyViolation) as ei:
        validate_url(url)
    assert ei.value.code == code


def test_port_restriction():
    with pytest.raises(PolicyViolation) as ei:
        check_destination("93.184.216.34", 22)
    assert ei.value.code == "PORT_NOT_ALLOWED"


@pytest.mark.parametrize(
    "answers",
    [["127.0.0.1"], ["93.184.216.34", "10.0.0.5"], ["169.254.169.254"], ["::ffff:10.0.0.1"]],
)
def test_hostname_rejected_if_any_answer_is_unsafe(answers):
    with pytest.raises(PolicyViolation):
        check_destination("evil.example", 443, resolver=lambda h, p: answers)


def test_returns_the_checked_ip_for_pinning():
    assert (
        check_destination("good.example", 443, resolver=lambda h, p: ["93.184.216.34"])
        == "93.184.216.34"
    )


@pytest.mark.parametrize("host", ["2130706433", "0x7f000001", "017700000001", "127.1"])
def test_numeric_ip_tricks_resolve_to_loopback_and_are_blocked(host):
    with pytest.raises(PolicyViolation):
        check_destination(host, 80)


def test_dev_allow_is_exact_ip_port_pairs_only():
    dev = DevAllow(frozenset({("127.0.0.1", 8020)}))
    assert check_destination("127.0.0.1", 8020, dev=dev) == "127.0.0.1"
    for port in (5432, 8010, 80):
        with pytest.raises(PolicyViolation):
            check_destination("127.0.0.1", port, dev=dev)


def test_dev_allow_refused_in_production(monkeypatch):
    monkeypatch.setenv("VERIFIER_DEV_ALLOW", "127.0.0.1:8020")
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RuntimeError):
        DevAllow.from_env()


def test_dns_failure_is_a_violation(monkeypatch):
    def boom(h, p):
        raise OSError("nxdomain")

    monkeypatch.setattr(netpolicy, "resolve", boom)
    with pytest.raises(PolicyViolation) as ei:
        check_destination("does-not-exist.invalid", 443)
    assert ei.value.code == "DNS_FAILURE"
