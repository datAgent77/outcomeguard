"""Governance over HTTP with real sessions (rehearsal gateway → payments are SIMULATED and
labeled as such). Checks roles, organization isolation and the dev-endpoint bypass."""

import pytest
from fastapi.testclient import TestClient

from outcomeguard.auth import load_principal
from tests.govhelpers import completed_run, locked_agreement_with_manual

OP = {"X-Operator-Token": "test-operator-token"}
PW = "correct horse battery staple"


@pytest.fixture
def http(make_session, session):
    from outcomeguard.main import create_app

    c = TestClient(create_app())
    seed = c.post("/dev/seed", json={"password": PW}, headers=OP).json()

    def h(name: str) -> dict:
        email = next(e for e in seed["users"] if e.startswith(name))
        tok = c.post("/auth/login", json={"email": email, "password": PW}).json()["token"]
        return {"Authorization": f"Bearer {tok}"}

    return c, seed, h


def test_governed_capture_over_http(http, session, tmp_path):
    c, seed, h = http

    def who(name):
        email = next(e for e in seed["users"] if e.startswith(name))
        return load_principal(session, seed["users"][email])

    from types import SimpleNamespace

    ids = SimpleNamespace(
        buyer_org_id=seed["buyer_organization_id"],
        merchant=SimpleNamespace(id=seed["merchant_account_id"]),
    )
    agreement = locked_agreement_with_manual(session, ids, who)

    # Payment: order for the locked version (amount from the version), simulated approval.
    r = c.post(f"/agreements/{agreement.id}/payments", headers=h("alice"))
    assert r.status_code == 201, r.text
    order = r.json()
    assert order["simulated"] is True and order["amount"] == "500.00"
    assert c.post(f"/agreements/{agreement.id}/payments", headers=h("bob")).status_code == 403
    c.post(f"/rehearsal/approve/{order['paypal_order_id']}")
    auth = c.post(f"/payments/orders/{order['id']}/authorize", headers=h("bob")).json()
    auth_id = auth["authorization"]["id"]

    # Verification run (fixed page measurements) → NEEDS_REVIEW.
    run = completed_run(session, who, agreement, tmp_path, form_ok=True)
    p = c.get(f"/payments/authorizations/{auth_id}/policy", headers=h("bob")).json()
    assert p["decision"] == "needs_review"
    assert [x["code"] for x in p["reasons"]] == ["MANUAL_REVIEW_PENDING"]
    assert c.get(f"/payments/authorizations/{auth_id}/policy", headers=h("eve")).status_code == 404
    r = c.post(
        f"/payments/authorizations/{auth_id}/capture-approvals",
        json={"inputs_sha256": p["inputs_sha256"]},
        headers=h("bob"),
    )
    assert r.status_code == 409 and r.json()["code"] == "POLICY_NOT_SATISFIED"

    # Human review by the buyer's reviewer, bound to the evidence snapshot.
    run_view = c.get(f"/verification/runs/{run.id}", headers=h("rita")).json()
    crit = next(x for x in run.results if x.criterion_key == "website_accessible").criterion_id
    body = {
        "criterion_id": crit,
        "decision": "satisfied",
        "rationale": "Manual WCAG 2.2 AA review: keyboard, contrast, labels OK.",
        "evidence_snapshot_sha256": run_view["evidence_snapshot_sha256"],
    }
    assert (
        c.post(
            f"/verification/runs/{run.id}/manual-reviews", json=body, headers=h("maya")
        ).status_code
        == 403
    )  # provider
    r = c.post(f"/verification/runs/{run.id}/manual-reviews", json=body, headers=h("rita"))
    assert r.status_code == 201, r.text

    # Buyer approval bound to the policy inputs; reviewer may not approve captures.
    p = c.get(f"/payments/authorizations/{auth_id}/policy", headers=h("bob")).json()
    assert p["decision"] == "eligible_for_approval"
    assert (
        c.post(
            f"/payments/authorizations/{auth_id}/capture-approvals",
            json={"inputs_sha256": p["inputs_sha256"]},
            headers=h("rita"),
        ).status_code
        == 403
    )
    a = c.post(
        f"/payments/authorizations/{auth_id}/capture-approvals",
        json={"inputs_sha256": p["inputs_sha256"]},
        headers=h("bob"),
    ).json()

    # Dev endpoint cannot bypass policy for agreement-bound payments.
    r = c.post(f"/dev/payments/authorizations/{auth_id}/capture", json={}, headers=OP)
    assert r.status_code == 403 and r.json()["code"] == "POLICY_REQUIRED"

    # Capture (simulated), then duplicate.
    assert (
        c.post(
            f"/payments/authorizations/{auth_id}/captures",
            json={"approval_id": a["approval_id"]},
            headers=h("maya"),
        ).status_code
        == 403
    )
    r = c.post(
        f"/payments/authorizations/{auth_id}/captures",
        json={"approval_id": a["approval_id"]},
        headers=h("bob"),
    )
    assert r.status_code == 200, r.text
    cap = r.json()
    assert cap["status"] == "completed" and cap["environment"] == "simulated"
    r = c.post(
        f"/payments/authorizations/{auth_id}/captures",
        json={"approval_id": a["approval_id"]},
        headers=h("bob"),
    )
    assert r.status_code in (409,) and r.json()["code"] in (
        "ALREADY_CAPTURED",
        "APPROVAL_ALREADY_USED",
    )
