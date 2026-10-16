"""Prepare the golden demo through the running API. Never creates a PayPal order.

    DEMO_PASSWORD=… uv run python scripts/prepare_demo.py [--api http://localhost:8010]

1. seeds the demo accounts (operator-chosen password, never printed);
2. creates a BACKUP agreement "Acme — spring landing page ($500)" with the canonical
   criteria, proposes and locks it (alice);
3. submits the BROKEN fixture, then the FIXED fixture as the provider (maya) and waits for
   the real verification worker (Chromium + Lighthouse);
4. records the accessibility review through the reviewer demo account — a SCRIPTED demo
   decision, labelled as such in its rationale and everywhere in the UI.

The live demo creates its own agreement with the AI wizard; this backup lets the presenter
jump to verified evidence if anything slow or flaky happens. Payment steps are left to
the presenter (simulated in rehearsal mode; real Sandbox only with a human approval).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

FIXTURES = "http://127.0.0.1:8020"
REQUEST = (
    "Build a mobile-friendly landing page for $500. The contact form must work, the website "
    "must be accessible and Lighthouse performance must score at least 90."
)
CRITERIA = [
    {
        "key": "site_available",
        "title": "Landing page is reachable",
        "spec": {"kind": "http_availability", "require_https": False},
    },
    {
        "key": "mobile_layout",
        "title": "Fits a 390 px phone screen without sideways scrolling",
        "spec": {"kind": "mobile_viewport"},
    },
    {
        "key": "contact_form_works",
        "title": "Contact form can be submitted",
        "spec": {
            "kind": "contact_form",
            "fields": [
                {"selector": "input[name=name]", "value": "OutcomeGuard Verifier"},
                {"selector": "input[name=email]", "value": "verifier@example.com"},
                {"selector": "textarea[name=message]", "value": "Automated check"},
            ],
            "success_text": "Thank you",
        },
    },
    {
        "key": "lighthouse_performance",
        "title": "Lighthouse performance score at least 90",
        "spec": {"kind": "lighthouse_performance", "min_score": 90},
    },
    {
        "key": "website_accessible",
        "title": "Website is accessible (WCAG 2.2 AA, human review)",
        "spec": {
            "kind": "manual_review",
            "instructions": "Check keyboard navigation, contrast, labels and headings against WCAG 2.2 AA.",
        },
    },
]
SCRIPTED = (
    "SCRIPTED DEMO DECISION: recorded by scripts/prepare_demo.py through the reviewer demo "
    "account. No human performed an accessibility review."
)


class Api:
    def __init__(self, base: str) -> None:
        if not base.startswith(("http://", "https://")):
            sys.exit("--api must be an http(s) URL")
        self.base, self.token = base.rstrip("/"), None

    def post_form(self, path: str) -> int:
        """POST without a body and without following the redirect (rehearsal approval)."""

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):  # type: ignore[no-untyped-def]
                return None

        opener = urllib.request.build_opener(NoRedirect)
        try:
            req = urllib.request.Request(self.base + path, method="POST", data=b"")  # noqa: S310
            return opener.open(req).status
        except urllib.error.HTTPError as e:
            return e.code

    def call(
        self, method: str, path: str, body: dict | None = None, headers: dict | None = None
    ) -> dict:
        req = urllib.request.Request(  # noqa: S310 — scheme validated in __init__
            self.base + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "content-type": "application/json",
                **({"authorization": f"Bearer {self.token}"} if self.token else {}),
                **(headers or {}),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 — http(s) only
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            sys.exit(f"{method} {path} -> {e.code}: {e.read().decode()[:300]}")

    def login(self, email: str, password: str) -> None:
        self.token = None
        self.token = self.call("POST", "/auth/login", {"email": email, "password": password})[
            "token"
        ]


def wait_run(api: Api, run_id: str) -> dict:
    for _ in range(150):
        r = api.call("GET", f"/verification/runs/{run_id}")
        if r["status"] in ("completed", "errored"):
            return r
        time.sleep(2)
    sys.exit(f"run {run_id} did not finish — is the worker running?")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8010")
    ap.add_argument("--web", default="http://localhost:3010")
    ap.add_argument(
        "--ready",
        action="store_true",
        help="rehearsal only: also create a SIMULATED order, approve it on the rehearsal page "
        "and authorize it, so the presenter only approves and captures (SIMULATED) on stage",
    )
    args = ap.parse_args()
    password = os.environ.get("DEMO_PASSWORD")
    if not password:
        sys.exit("set DEMO_PASSWORD")
    api = Api(args.api)
    mode = api.call("GET", "/ready")["config"]["payment_mode"]
    print(f"API {args.api} · payment mode {mode}")
    if args.ready and mode != "rehearsal":
        sys.exit("--ready creates a payment and is allowed only in rehearsal mode (SIMULATED)")

    env = {**os.environ}
    if mode == "rehearsal":
        env["PAYMENT_MODE"] = "rehearsal"
    subprocess.run(  # noqa: S603 — fixed argv: this interpreter + the seed script
        [sys.executable, str(Path(__file__).with_name("seed_demo.py"))], check=True, env=env
    )

    t0 = time.monotonic()
    api.login("alice@acme.demo", password)
    me = api.call("GET", "/workspace/context")
    merchant = api.call("GET", "/workspace/merchants")[0]
    stamp = datetime.now(UTC).strftime("%b %d %H:%M")
    a = api.call(
        "POST",
        "/agreements",
        {
            "buyer_organization_id": next(
                o["id"] for o in me["organizations"] if o["kind"] == "buyer"
            ),
            "merchant_account_id": merchant["id"],
            "title": f"Acme — spring landing page ($500) · backup {stamp}",
            "requirements_text": REQUEST,
            "amount": "500.00",
            "delivery_deadline": (datetime.now(UTC) + timedelta(days=14)).isoformat(),
            "criteria": CRITERIA,
        },
    )
    v = api.call(
        "POST",
        f"/agreements/{a['id']}/versions/1/propose",
        {"expected_revision": a["versions"][0]["revision"]},
    )
    api.call(
        "POST",
        f"/agreements/{a['id']}/versions/1/approve",
        {"content_hash": v["versions"][0]["content_hash"]},
    )
    print(f"✔ agreement locked {a['id']}")

    api.login("maya@northwind.demo", password)
    timings = {}
    for variant in ("landing-page-broken", "landing-page-fixed"):
        t = time.monotonic()
        run = api.call(
            "POST", f"/agreements/{a['id']}/evidence", {"delivery_url": f"{FIXTURES}/{variant}/"}
        )
        run = wait_run(api, run["id"])
        timings[variant] = round(time.monotonic() - t, 1)
        print(f"✔ {variant}: {run['status']} / {run['verdict']} in {timings[variant]} s")
    if run["verdict"] != "needs_manual_review":
        sys.exit(f"unexpected verdict for the fixed page: {run['verdict']}")

    api.login("rita@acme.demo", password)
    crit = next(r["criterion_id"] for r in run["results"] if r["kind"] == "manual_review")
    api.call(
        "POST",
        f"/verification/runs/{run['id']}/manual-reviews",
        {
            "criterion_id": crit,
            "decision": "satisfied",
            "rationale": SCRIPTED,
            "evidence_snapshot_sha256": run["evidence_snapshot_sha256"],
        },
    )
    print("✔ accessibility review recorded — SCRIPTED demo decision (labelled)")
    if args.ready:
        api.login("alice@acme.demo", password)
        order = api.call(
            "POST",
            f"/agreements/{a['id']}/payments",
            {},
            {"idempotency-key": f"demo-{a['id'][:8]}"},
        )
        assert order["simulated"] and order["paypal_order_id"].startswith("SIM-")
        status = api.post_form(f"/rehearsal/approve/{order['paypal_order_id']}")
        if status != 303:
            sys.exit(f"rehearsal approval failed: {status}")
        auth = api.call("POST", f"/payments/orders/{order['id']}/authorize")["authorization"]
        print(
            f"✔ SIMULATED order {order['paypal_order_id']} approved (rehearsal page) and authorized "
            f"{auth['paypal_authorization_id']} — do not restart the API before the capture"
        )
    print(
        json.dumps(
            {
                "agreement_id": a["id"],
                "url": f"{args.web}/agreements/{a['id']}?tab=evidence",
                "timings_s": timings,
                "total_s": round(time.monotonic() - t0, 1),
            },
            indent=2,
        )
    )
    if args.ready:
        print("Only a SIMULATED rehearsal order was created; no PayPal call was made.")
    else:
        print("No PayPal order was created. Payment steps are performed by the presenter.")


if __name__ == "__main__":
    main()
