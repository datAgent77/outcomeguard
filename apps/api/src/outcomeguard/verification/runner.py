"""Verification runner: measures, never decides.

Reads one job spec (JSON) on stdin, runs fixed checks against the submitted site, writes
artifacts into the given directory and prints measurements (JSON) on stdout. It has no
database access, no PayPal credentials and refuses to start if any are in its environment.
All its traffic — HTTPX, Chromium, Lighthouse — goes through the egress guard proxy.

    python -m outcomeguard.verification.runner < job.json > result.json

Verdicts are computed by the orchestrator (domain/verdict.py) from these measurements.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from outcomeguard.verification.netpolicy import PolicyViolation, validate_url
from outcomeguard.verification.proxy import EgressProxy, ThreadedProxy

FORBIDDEN_ENV = re.compile(r"^(PAYPAL_|DATABASE_URL|SESSION_SECRET|OPERATOR_TOKEN|SINK_SECRET)")
BLOCK_HEADER = "x-outcomeguard-blocked"
MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/156.0.0.0 Mobile Safari/537.36 OutcomeGuardVerifier/1.0"
)
CHROMIUM_ARGS = [
    "--proxy-bypass-list=<-loopback>",  # loopback must go through the proxy too
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--dns-prefetch-disable",
    "--disable-background-networking",
    "--no-pings",
    "--disable-component-update",
    "--disable-sync",
    "--disable-default-apps",
    "--disable-domain-reliability",
    "--disable-client-side-phishing-detection",
    "--no-default-browser-check",
    "--no-first-run",
    "--disable-features=NetworkPrediction,InterestFeedContentSuggestions,OptimizationHints,"
    "MediaRouter,AutofillServerCommunication,Translate,SignInProfileCreation,"
    "ChromeSignin,PrivacySandboxSettings4",
]


def chromium_sandbox() -> bool:
    """Chromium's own sandbox is ON by default (host). Containers without user namespaces
    set VERIFIER_CHROMIUM_SANDBOX=0; there the container (non-root, internal network whose
    only peer is the egress proxy) is the isolation boundary instead."""
    return os.environ.get("VERIFIER_CHROMIUM_SANDBOX", "1") != "0"


def assert_clean_environment(env: dict[str, str] | None = None) -> None:
    env = dict(os.environ if env is None else env)
    leaked = sorted(k for k in env if FORBIDDEN_ENV.match(k))
    if leaked:
        raise SystemExit(f"runner refuses to start: forbidden variables in environment: {leaked}")


def target_url(delivery_url: str, path: str) -> str:
    if path in ("", "/"):
        return delivery_url
    base = delivery_url if delivery_url.endswith("/") else delivery_url + "/"
    return urljoin(base, path.lstrip("/"))


class Artifacts:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.items: list[dict[str, Any]] = []

    def add(
        self, *, label: str, kind: str, data: bytes, content_type: str, criterion: str | None
    ) -> str:
        name = f"{len(self.items):02d}-{re.sub(r'[^a-z0-9_.-]', '_', label.lower())}"
        path = self.root / name
        path.write_bytes(data)
        self.items.append(
            {
                "label": label,
                "kind": kind,
                "file": name,
                "content_type": content_type,
                "criterion_id": criterion,
            }
        )
        return name


# ------------------------------------------------------------------------- HTTP


def check_http(url: str, spec: dict[str, Any], proxy_url: str, arts: Artifacts, cid: str) -> dict:
    chain: list[dict[str, Any]] = []
    t0 = time.monotonic()
    try:
        with httpx.Client(
            proxy=proxy_url, timeout=spec["timeout_ms"] / 1000, follow_redirects=False, verify=True
        ) as client:
            current = url
            for _ in range(spec["max_redirects"] + 2):
                validate_url(current)
                resp = client.get(current, headers={"User-Agent": MOBILE_UA})
                if resp.headers.get(BLOCK_HEADER):
                    return {
                        "error": "destination blocked",
                        "error_code": "BLOCKED_BY_EGRESS_POLICY",
                        "blocked": resp.headers[BLOCK_HEADER],
                        "chain": chain,
                    }
                hop = {
                    "url": current,
                    "status": resp.status_code,
                    "location": resp.headers.get("location"),
                }
                chain.append(hop)
                if resp.is_redirect and hop["location"]:
                    current = urljoin(current, hop["location"])
                    continue
                break
    except PolicyViolation as exc:
        return {"error": str(exc), "error_code": exc.code, "chain": chain}
    except httpx.ProxyError as exc:
        return {"error": str(exc)[:300], "error_code": "BLOCKED_BY_EGRESS_POLICY", "chain": chain}
    except httpx.HTTPError as exc:
        return {
            "error": f"{type(exc).__name__}: {str(exc)[:200]}",
            "error_code": "HTTP_ERROR",
            "chain": chain,
        }
    m = {"chain": chain, "elapsed_ms": int((time.monotonic() - t0) * 1000)}
    arts.add(
        label="http-trace.json",
        kind="http_trace",
        data=json.dumps(m, indent=1).encode(),
        content_type="application/json",
        criterion=cid,
    )
    return m


# -------------------------------------------------------------------- browser


def _new_context(browser, viewport: dict[str, int] | None, mobile: bool):
    ctx = browser.new_context(
        viewport=viewport or {"width": 1280, "height": 800},
        is_mobile=mobile,
        has_touch=mobile,
        user_agent=MOBILE_UA if mobile else None,
        service_workers="block",
        accept_downloads=False,
    )
    # No WebSockets from submitted pages (they bypass request routing). A routed socket
    # whose handler never calls connect_to_server() is mocked: no connection is opened.
    ctx.route_web_socket(re.compile(".*"), lambda ws: None)
    return ctx


def _blocked_by_policy(response) -> str | None:
    if response is None:
        return None
    return response.headers.get(BLOCK_HEADER)


def check_mobile(browser, url: str, spec: dict[str, Any], arts: Artifacts, cid: str) -> dict:
    ctx = _new_context(browser, spec["viewport"], mobile=True)
    page = ctx.new_page()
    page.on("dialog", lambda d: d.dismiss())
    try:
        try:
            resp = page.goto(url, wait_until="load", timeout=spec["timeout_ms"])
        except Exception as exc:  # noqa: BLE001 — navigation failure is a measurement
            return {"error": str(exc).splitlines()[0][:300], "error_code": "NAVIGATION_FAILED"}
        if code := _blocked_by_policy(resp):
            return {
                "error": "page blocked",
                "error_code": "BLOCKED_BY_EGRESS_POLICY",
                "blocked": code,
            }
        page.wait_for_timeout(300)
        dims = page.evaluate(
            "() => ({sw: document.documentElement.scrollWidth, vw: window.innerWidth})"
        )
        visible = {
            sel: page.locator(sel).first.is_visible() if page.locator(sel).count() else False
            for sel in spec.get("required_visible_selectors", [])
        }
        shot = page.screenshot(full_page=True)
        arts.add(
            label="mobile-viewport.png",
            kind="screenshot",
            data=shot,
            content_type="image/png",
            criterion=cid,
        )
        return {
            "scroll_width": dims["sw"],
            "viewport_width": dims["vw"],
            "http_status": resp.status if resp else None,
            "selectors_visible": visible,
        }
    finally:
        ctx.close()


def check_contact_form(
    browser, url: str, spec: dict[str, Any], sink: dict[str, str] | None, arts: Artifacts, cid: str
) -> dict:
    if not sink:
        return {"unverifiable": "NO_SINK_CONFIGURED"}
    sink_parts = urlsplit(sink["url"])
    test_values = [f["value"] for f in spec["fields"] if len(f["value"]) >= 4]
    prohibited: list[dict[str, str]] = []
    unrelated_blocked: list[dict[str, str]] = []
    sink_requests: list[dict[str, Any]] = []

    def is_sink(u: str) -> bool:
        p = urlsplit(u)
        return (p.scheme, p.hostname, p.port, p.path.rstrip("/")) == (
            sink_parts.scheme,
            sink_parts.hostname,
            sink_parts.port,
            sink_parts.path.rstrip("/"),
        )

    def carries_test_data(req) -> bool:
        blob = req.url + (req.post_data or "")
        return any(
            v in blob or v.replace(" ", "+") in blob or v.replace("@", "%40") in blob
            for v in test_values
        )

    def route_handler(route):
        req = route.request
        if is_sink(req.url):
            if req.method == "POST":
                headers = {**req.headers, "x-outcomeguard-sink-token": sink["token"]}
                return route.continue_(headers=headers)
            return route.continue_()
        if req.method != "GET" or carries_test_data(req):
            entry = {"method": req.method, "url": req.url[:300]}
            # Never transmitted: aborted inside the browser before any byte leaves.
            (
                prohibited
                if carries_test_data(req) or req.resource_type == "document"
                else unrelated_blocked
            ).append(entry)
            return route.abort("blockedbyclient")
        return route.continue_()

    ctx = _new_context(browser, None, mobile=False)
    ctx.route("**/*", route_handler)
    page = ctx.new_page()
    page.on("dialog", lambda d: d.dismiss())
    page.on(
        "response",
        lambda r: (
            sink_requests.append({"status": r.status})
            if is_sink(r.url) and r.request.method == "POST"
            else None
        ),
    )
    m: dict[str, Any] = {}
    try:
        try:
            resp = page.goto(url, wait_until="load", timeout=spec["timeout_ms"])
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc).splitlines()[0][:300], "error_code": "NAVIGATION_FAILED"}
        if code := _blocked_by_policy(resp):
            return {
                "error": "page blocked",
                "error_code": "BLOCKED_BY_EGRESS_POLICY",
                "blocked": code,
            }
        form = page.locator(spec["form_selector"])
        m["form_found"] = form.count() > 0
        if not m["form_found"]:
            for frame in page.frames[1:]:
                if frame.locator(spec["form_selector"]).count():
                    return {"unverifiable": "FORM_IN_SUBFRAME"}
            return m
        missing = []
        for f in spec["fields"]:
            loc = form.first.locator(f["selector"])
            if loc.count() == 0:
                missing.append(f["selector"])
                continue
            loc.first.fill(f["value"])
        m["fields_missing"] = missing
        if missing:
            return m
        submit = form.first.locator(spec["submit_selector"])
        if submit.count() == 0:
            m["fields_missing"] = [spec["submit_selector"]]
            return m
        submit.first.click()
        try:
            page.get_by_text(spec["success_text"]).first.wait_for(
                state="visible", timeout=min(spec["timeout_ms"], 10_000)
            )
            m["success_text_seen"] = True
        except Exception:  # noqa: BLE001 — not seen within the timeout
            m["success_text_seen"] = False
        page.wait_for_timeout(500)
        shot = page.screenshot(full_page=True)
        arts.add(
            label="contact-form-after-submit.png",
            kind="screenshot",
            data=shot,
            content_type="image/png",
            criterion=cid,
        )
        return m
    finally:
        m["prohibited_requests"] = prohibited
        m["unrelated_blocked_requests"] = unrelated_blocked
        m["sink_requests"] = sink_requests
        ctx.close()


# ----------------------------------------------------------------- Lighthouse


def headless_shell_for(chrome_path: str) -> str:
    """Prefer Playwright's chrome-headless-shell for Lighthouse: unlike full Chrome it has no
    sign-in, component-updater or other background services that call home."""
    root = Path(chrome_path)
    for parent in root.parents:
        if parent.name == "ms-playwright" or (parent / ".links").exists():
            for cand in sorted(parent.glob("chromium_headless_shell-*/*/chrome-headless-shell")):
                return str(cand)
            break
    return chrome_path


def lighthouse_once(lh_bin: str, chrome_path: str, url: str, proxy_url: str, out: Path) -> dict:
    shell = headless_shell_for(chrome_path)
    headless = [] if shell.endswith("chrome-headless-shell") else ["--headless=new"]
    chrome_path = shell
    no_sandbox = [] if chromium_sandbox() else ["--no-sandbox"]
    flags = " ".join([*headless, *no_sandbox, f"--proxy-server={proxy_url}", *CHROMIUM_ARGS])
    cmd = [
        lh_bin,
        url,
        "--quiet",
        "--output=json",
        f"--output-path={out}",
        "--only-categories=performance",
        "--form-factor=mobile",
        "--throttling-method=simulate",
        "--max-wait-for-load=45000",
        f"--chrome-flags={flags}",
    ]
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", "/tmp"),  # noqa: S108
        "CHROME_PATH": chrome_path,
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),  # noqa: S108
    }
    t0 = time.monotonic()
    proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
        cmd, capture_output=True, text=True, timeout=180, env=env, check=False
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    if proc.returncode != 0 or not out.exists():
        return {"error": (proc.stderr or proc.stdout)[-300:], "elapsed_ms": elapsed}
    lhr = json.loads(out.read_text())
    if lhr.get("runtimeError"):
        return {"error": str(lhr["runtimeError"])[:300], "elapsed_ms": elapsed}
    score = (lhr.get("categories", {}).get("performance") or {}).get("score")
    if score is None:
        return {"error": "no performance score", "elapsed_ms": elapsed}
    return {
        "score": round(score * 100),
        "elapsed_ms": elapsed,
        "lighthouse_version": lhr.get("lighthouseVersion"),
        "final_url": lhr.get("finalDisplayedUrl"),
    }


def check_lighthouse(
    url: str,
    spec: dict[str, Any],
    cfg: dict[str, Any],
    chrome_path: str,
    proxy_url: str,
    arts: Artifacts,
    cid: str,
    workdir: Path,
) -> dict:
    from outcomeguard.domain.verdict import lighthouse_needs_more_runs

    band = int(cfg.get("band", 5))
    runs: list[dict[str, Any]] = []

    def one(i: int) -> dict:
        out = workdir / f"lhr-{cid[:8]}-{i}.json"
        r = lighthouse_once(cfg["bin"], chrome_path, url, proxy_url, out)
        if out.exists():
            arts.add(
                label=f"lighthouse-run-{i + 1}.json.gz",
                kind="lighthouse_json",
                data=gzip.compress(out.read_bytes()),
                content_type="application/gzip",
                criterion=cid,
            )
            out.unlink()
        runs.append(r)
        return r

    first = one(0)
    if "error" in first:
        return {
            "error": first["error"],
            "error_code": "LIGHTHOUSE_FAILED",
            "runs": runs,
            "band": band,
        }
    if lighthouse_needs_more_runs(first["score"], spec["min_score"], band):
        for i in (1, 2):
            r = one(i)
            if "error" in r:
                return {
                    "error": r["error"],
                    "error_code": "LIGHTHOUSE_FAILED",
                    "runs": runs,
                    "band": band,
                }
    return {
        "scores": [r["score"] for r in runs],
        "runs": runs,
        "band": band,
        "adaptive_retry": len(runs) > 1,
    }


# ------------------------------------------------------------------------ main


def run(job: dict[str, Any]) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    workdir = Path(job["artifacts_dir"])
    arts = Artifacts(workdir)
    measurements: dict[str, Any] = {}
    timings: dict[str, int] = {}
    tool_versions: dict[str, Any] = {"httpx": httpx.__version__}
    external_proxy = job.get("proxy_url")
    proxy_ctx = None if external_proxy else ThreadedProxy(EgressProxy())
    if proxy_ctx:
        proxy_ctx.__enter__()
    proxy_url = external_proxy or proxy_ctx.url  # type: ignore[union-attr]
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                proxy={"server": proxy_url, "bypass": "<-loopback>"},
                args=CHROMIUM_ARGS,
                chromium_sandbox=chromium_sandbox(),
            )
            tool_versions["chromium"] = browser.version
            tool_versions["chromium_sandbox"] = chromium_sandbox()
            chrome_path = pw.chromium.executable_path
            try:
                for c in job["criteria"]:
                    cid, kind, spec = c["criterion_id"], c["kind"], c["spec"]
                    url = target_url(job["delivery_url"], spec.get("path", "/"))
                    t0 = time.monotonic()
                    try:
                        validate_url(url)
                        if kind == "http_availability":
                            m = check_http(url, spec, proxy_url, arts, cid)
                        elif kind == "mobile_viewport":
                            m = check_mobile(browser, url, spec, arts, cid)
                        elif kind == "contact_form":
                            m = check_contact_form(
                                browser,
                                url,
                                spec,
                                job.get("sinks", {}).get(spec["test_endpoint_ref"]),
                                arts,
                                cid,
                            )
                        elif kind == "lighthouse_performance":
                            m = check_lighthouse(
                                url,
                                spec,
                                job["lighthouse"],
                                chrome_path,
                                proxy_url,
                                arts,
                                cid,
                                workdir,
                            )
                        else:
                            continue
                    except PolicyViolation as exc:
                        m = {"error": str(exc), "error_code": exc.code}
                    except Exception as exc:  # noqa: BLE001 — never crash the whole run
                        m = {
                            "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                            "error_code": "RUNNER_EXCEPTION",
                        }
                    timings[cid] = int((time.monotonic() - t0) * 1000)
                    measurements[cid] = m
            finally:
                browser.close()
    finally:
        decisions = proxy_ctx.proxy.stats.snapshot() if proxy_ctx else []
        if proxy_ctx:
            proxy_ctx.__exit__(None, None, None)
    if decisions:
        arts.add(
            label="egress-decisions.json",
            kind="network_log",
            data=json.dumps(decisions, indent=1).encode(),
            content_type="application/json",
            criterion=None,
        )
    lh_pkg = Path(job["lighthouse"]["bin"]).parent.parent / "lighthouse" / "package.json"
    if lh_pkg.exists():
        tool_versions["lighthouse"] = json.loads(lh_pkg.read_text()).get("version")
    try:
        from importlib.metadata import version

        tool_versions["playwright"] = version("playwright")
    except Exception:  # noqa: BLE001, S110 — version is informational
        pass
    return {
        "measurements": measurements,
        "artifacts": arts.items,
        "timings_ms": timings,
        "tool_versions": tool_versions,
        "egress_blocked": [d for d in decisions if d.get("action") == "block"],
    }


def run_inline(job: dict[str, Any]) -> dict[str, Any]:
    """Container mode: run in a private temp dir and return artifacts inline (base64), so
    the runner container needs no shared filesystem with the worker."""
    import base64
    import tempfile

    with tempfile.TemporaryDirectory(prefix="og-job-") as tmp:
        job = {**job, "artifacts_dir": tmp}
        result = run(job)
        for item in result["artifacts"]:
            item["data_b64"] = base64.b64encode((Path(tmp) / item["file"]).read_bytes()).decode()
    return result


def serve(listen: str) -> None:
    """Minimal job server for the isolated runner container. One job at a time; the only
    client is the verification worker on the sandbox network."""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    max_body = 256 * 1024

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a: object) -> None:
            pass

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("content-length") or 0)
            if self.path != "/run" or not 0 < length <= max_body:
                self.send_error(400)
                return
            try:
                body = json.dumps(run_inline(json.loads(self.rfile.read(length)))).encode()
                code = 200
            except Exception as exc:  # noqa: BLE001 — report, never crash the server
                body = json.dumps({"error": f"{type(exc).__name__}: {exc}"[:500]}).encode()
                code = 500
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    host, _, port = listen.rpartition(":")
    HTTPServer((host, int(port)), Handler).serve_forever()


def main() -> None:
    assert_clean_environment()
    if len(sys.argv) == 3 and sys.argv[1] == "--serve":
        serve(sys.argv[2])
        return
    job = json.loads(sys.stdin.read())
    result = run(job)
    sys.stdout.write(json.dumps(result))


if __name__ == "__main__":
    main()
