"""Runs INSIDE the runner container (scripts/container_isolation_test.sh). Every probe
records what actually happened; the shell script asserts on the JSON it prints."""

from __future__ import annotations

import json
import socket
import sys
import time

import httpx

PROXY = "http://proxy:3128"
FIXTURE = "http://172.31.250.10:8020/landing-page-fixed/"
results: dict[str, dict] = {}


def probe(name: str, fn) -> None:
    t0 = time.monotonic()
    try:
        results[name] = {"ok": True, "detail": fn()}
    except Exception as exc:  # noqa: BLE001
        results[name] = {"ok": False, "detail": f"{type(exc).__name__}: {str(exc)[:160]}"}
    results[name]["ms"] = int((time.monotonic() - t0) * 1000)


def tcp(host: str, port: int) -> str:
    with socket.create_connection((host, port), timeout=4):
        return "connected"


def via_proxy(url: str) -> str:
    r = httpx.get(url, proxy=PROXY, timeout=15)
    return f"{r.status_code} blocked={r.headers.get('x-outcomeguard-blocked')}"


# --- direct network access from the runner (must all fail) ---------------------------
probe("direct_tcp_internet_1.1.1.1_443", lambda: tcp("1.1.1.1", 443))
probe("direct_tcp_fixture_172.31.250.10_8020", lambda: tcp("172.31.250.10", 8020))
probe("direct_tcp_internal_svc_8000", lambda: tcp("internal-svc", 8000))
probe("direct_http_example_com", lambda: httpx.get("http://example.com/", timeout=5).status_code)
probe("dns_resolve_internal_svc", lambda: socket.gethostbyname("internal-svc"))
probe("tcp_proxy_3128_reachable", lambda: tcp("proxy", 3128))

# --- through the egress proxy (policy decides) ----------------------------------------
probe("proxy_fixture_allowed", lambda: via_proxy(FIXTURE))
probe("proxy_internet_example_com", lambda: via_proxy("http://example.com/"))
probe("proxy_internal_svc", lambda: via_proxy("http://internal-svc:8000/"))
probe("proxy_metadata", lambda: via_proxy("http://169.254.169.254/latest/meta-data/"))
probe("proxy_gateway_private", lambda: via_proxy("http://172.31.250.1/"))
probe("proxy_fixture_other_port", lambda: via_proxy("http://172.31.250.10:22/"))
probe("proxy_loopback_of_proxy", lambda: via_proxy("http://127.0.0.1:3128/"))


# --- Chromium: with proxy works, with proxy bypass has no route -----------------------
def chromium(proxy: bool, url: str) -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        kw = {"proxy": {"server": PROXY, "bypass": "<-loopback>"}} if proxy else {
            "args": ["--no-proxy-server"]}
        b = p.chromium.launch(**kw)
        try:
            page = b.new_page()
            resp = page.goto(url, timeout=15000)
            return f"status={resp.status if resp else None} title={page.title()[:40]!r}"
        finally:
            b.close()


probe("chromium_with_proxy_fixture", lambda: chromium(True, FIXTURE))
probe("chromium_no_proxy_bypass_fixture", lambda: chromium(False, FIXTURE))
probe("chromium_no_proxy_bypass_internet", lambda: chromium(False, "http://example.com/"))


# --- a real verification job inside the container -------------------------------------
def real_job() -> dict:
    from outcomeguard.verification.runner import run_inline

    job = {
        "run_id": "container", "delivery_url": FIXTURE, "proxy_url": PROXY,
        "criteria": [
            {"criterion_id": "http", "key": "site_available", "kind": "http_availability",
             "spec": {"path": "/", "expected_status": [200], "max_redirects": 5,
                      "require_https": False, "timeout_ms": 10000}},
            {"criterion_id": "mobile", "key": "mobile_layout", "kind": "mobile_viewport",
             "spec": {"path": "/", "viewport": {"width": 390, "height": 844},
                      "no_horizontal_overflow": True, "required_visible_selectors": ["form"],
                      "timeout_ms": 20000}},
            {"criterion_id": "lh", "key": "lighthouse_performance",
             "kind": "lighthouse_performance",
             "spec": {"path": "/", "min_score": 90, "form_factor": "mobile", "runs": 3,
                      "throttling": "simulate"}},
        ],
        "sinks": {},
        "lighthouse": {"bin": "/app/verifier-tools/node_modules/.bin/lighthouse", "band": 5},
    }
    out = run_inline(job)
    m = out["measurements"]
    return {"http": m["http"].get("chain"), "mobile": {k: m["mobile"].get(k) for k in
            ("scroll_width", "viewport_width", "selectors_visible", "error")},
            "lighthouse_scores": m["lh"].get("scores"), "lighthouse_error": m["lh"].get("error"),
            "lighthouse_ms": [r.get("elapsed_ms") for r in m["lh"].get("runs", [])],
            "tool_versions": out["tool_versions"],
            "artifacts": [(a["label"], len(a["data_b64"])) for a in out["artifacts"]]}


probe("real_verification_job", real_job)
json.dump(results, sys.stdout, indent=1)
