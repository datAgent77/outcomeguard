"""Real Chromium (through the real runner code path): submitted-page JavaScript must not
reach internal services, metadata endpoints or private networks, including via WebSocket,
beacon, iframe, image and Chrome's default loopback proxy bypass.

Proven here at the proxy layer on the host. Container-level network isolation (no route
except the proxy) is tested separately in scripts/container_isolation_test.sh."""

import json
import os

import pytest

from outcomeguard.verification import runner
from tests.vhelpers import Canary

pytestmark = pytest.mark.browser

LH_BIN = os.path.join(
    os.path.dirname(__file__), "..", "verifier-tools", "node_modules", ".bin", "lighthouse"
)


def attack_page(internal_port: int) -> bytes:
    t = f"http://127.0.0.1:{internal_port}"
    return f"""<!doctype html><meta name=viewport content="width=device-width">
<h1>innocent landing page</h1><form><input name=email></form>
<img src="{t}/img"><iframe src="{t}/frame"></iframe>
<script>
  const T = {json.dumps(t)};
  const tries = [
    () => fetch(T + "/fetch"),
    () => fetch("http://169.254.169.254/latest/meta-data/"),
    () => fetch("http://10.0.0.1/"),
    () => fetch("http://[::1]:{internal_port}/v6"),
    () => fetch("http://localhost:{internal_port}/localhost"),
    () => fetch("http://2130706433:{internal_port}/decimal"),
    () => navigator.sendBeacon(T + "/beacon", "x"),
    () => new WebSocket("ws://127.0.0.1:{internal_port}/ws"),
    () => {{ const x = new XMLHttpRequest(); x.open("GET", T + "/xhr"); x.send(); }},
  ];
  for (const t of tries) {{ try {{ t(); }} catch (e) {{}} }}
</script>""".encode()


@pytest.fixture
def internal():
    c = Canary()
    yield c
    c.close()


def test_page_javascript_cannot_reach_internal_services(internal, monkeypatch, tmp_path):
    site = Canary({"/": ("text/html", attack_page(internal.port))})
    try:
        monkeypatch.setenv("VERIFIER_DEV_ALLOW", f"127.0.0.1:{site.port}")
        job = {
            "run_id": "iso",
            "delivery_url": f"{site.base}/",
            "artifacts_dir": str(tmp_path),
            "criteria": [
                {
                    "criterion_id": "m",
                    "key": "m",
                    "kind": "mobile_viewport",
                    "spec": {
                        "path": "/",
                        "viewport": {"width": 390, "height": 844},
                        "no_horizontal_overflow": True,
                        "required_visible_selectors": [],
                        "timeout_ms": 20000,
                    },
                }
            ],
            "sinks": {},
            "lighthouse": {"bin": LH_BIN, "band": 5},
        }
        out = runner.run(job)
    finally:
        site.close()
    assert out["measurements"]["m"].get("scroll_width"), out["measurements"]
    assert internal.hits == [], f"internal service was reached: {internal.hits}"
    blocked = {d.get("code") for d in out["egress_blocked"]}
    assert {"PORT_NOT_ALLOWED", "METADATA_ADDRESS"} <= blocked, blocked


def test_runner_refuses_credentials_in_environment():
    for var in ("PAYPAL_CLIENT_SECRET", "DATABASE_URL", "SESSION_SECRET", "SINK_SECRET"):
        with pytest.raises(SystemExit):
            runner.assert_clean_environment({"PATH": "/bin", var: "x"})
    runner.assert_clean_environment({"PATH": "/bin", "VERIFIER_DEV_ALLOW": ""})


def test_lighthouse_adaptive_retry_with_real_lighthouse(monkeypatch, tmp_path):
    """Real Lighthouse: a score far from the threshold → 1 run; within ±band → 3 runs and
    the median decides."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "fixtures"))
    import serve

    srv = serve.start(0, "http://127.0.0.1:9/sinks/default")
    port = srv.server_address[1]
    monkeypatch.setenv("VERIFIER_DEV_ALLOW", f"127.0.0.1:{port}")
    url = f"http://127.0.0.1:{port}/landing-page-fixed/"

    def job(min_score):
        return {
            "run_id": "lh",
            "delivery_url": url,
            "artifacts_dir": str(tmp_path),
            "criteria": [
                {
                    "criterion_id": "lh",
                    "key": "lh",
                    "kind": "lighthouse_performance",
                    "spec": {
                        "path": "/",
                        "min_score": min_score,
                        "form_factor": "mobile",
                        "runs": 3,
                        "throttling": "simulate",
                    },
                }
            ],
            "sinks": {},
            "lighthouse": {"bin": LH_BIN, "band": 5},
        }

    try:
        far = runner.run(job(50))["measurements"]["lh"]
        first = far["scores"][0]
        near = runner.run(job(max(0, first - 3)))["measurements"]["lh"]
    finally:
        srv.shutdown()
    print("\nlighthouse far:", far["scores"], [r["elapsed_ms"] for r in far["runs"]])
    print("lighthouse near:", near["scores"], [r["elapsed_ms"] for r in near["runs"]])
    assert len(far["scores"]) == 1 and far["adaptive_retry"] is False
    assert len(near["scores"]) == 3 and near["adaptive_retry"] is True
