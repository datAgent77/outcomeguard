"""P07 read-only workspace endpoints and the two small write-path additions (payment
Idempotency-Key, AI suggestion opt-in over HTTP). Rehearsal gateway → payments are
SIMULATED and labeled; the AI provider is a scripted mock."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from outcomeguard.ai.provider import ScriptedProvider
from outcomeguard.auth import load_principal
from tests.govhelpers import completed_run, locked_agreement_with_manual
from tests.test_ai import crit, draft_json

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

    def who(name):
        email = next(e for e in seed["users"] if e.startswith(name))
        return load_principal(session, seed["users"][email])

    ids = SimpleNamespace(
        buyer_org_id=seed["buyer_organization_id"],
        merchant=SimpleNamespace(id=seed["merchant_account_id"]),
    )
    return c, seed, h, who, ids


def test_context_and_merchant_directory(http):
    c, seed, h, _, _ = http
    me = c.get("/workspace/context", headers=h("rita")).json()
    assert me["email"] == "rita@acme.demo" and me["kind"] == "human" and me["demo_account"]
    assert [o["roles"] for o in me["organizations"]] == [["buyer_reviewer"]]
    m = c.get("/workspace/merchants", headers=h("alice")).json()
    assert [x["id"] for x in m] == [seed["merchant_account_id"]]
    assert "paypal_merchant_id" not in m[0]
    assert c.get("/workspace/context").status_code == 401


def test_overview_ledger_and_scoping(http, session, tmp_path):
    c, seed, h, who, ids = http
    agreement = locked_agreement_with_manual(session, ids, who)

    # Idempotent payment creation: same key → same order; new key → new order.
    hk = {**h("alice"), "Idempotency-Key": "pay-key-00000001"}
    o1 = c.post(f"/agreements/{agreement.id}/payments", headers=hk)
    o2 = c.post(f"/agreements/{agreement.id}/payments", headers=hk)
    assert o1.status_code == 201 and o2.status_code == 201
    assert o1.json()["id"] == o2.json()["id"] and o1.json()["simulated"] is True
    o3 = c.post(
        f"/agreements/{agreement.id}/payments",
        headers={**h("alice"), "Idempotency-Key": "pay-key-00000002"},
    )
    assert o3.json()["id"] != o1.json()["id"]
    orders = c.get(f"/agreements/{agreement.id}/payments", headers=h("maya")).json()
    assert {o["id"] for o in orders} == {o1.json()["id"], o3.json()["id"]}
    assert c.get(f"/agreements/{agreement.id}/payments", headers=h("eve")).status_code == 404

    order = o3.json()
    c.post(f"/rehearsal/approve/{order['paypal_order_id']}")
    auth_id = c.post(f"/payments/orders/{order['id']}/authorize", headers=h("bob")).json()[
        "authorization"
    ]["id"]

    run = completed_run(session, who, agreement, tmp_path, form_ok=True)
    rv = c.get(f"/verification/runs/{run.id}", headers=h("rita")).json()
    manual = next(r for r in rv["results"] if r["criterion_key"] == "website_accessible")
    assert manual["criterion_id"] and isinstance(rv["form_receipts"], list)

    # Overview before review: one item awaiting review; no persisted policy yet.
    ov = c.get("/workspace/agreements", headers=h("alice")).json()
    row = next(x for x in ov if x["id"] == agreement.id)
    assert row["perspective"] == "buyer" and row["locked"] is True
    assert row["latest_completed_run"]["awaiting_review"] == 1
    assert row["payment"]["authorization_status"] == "authorized"
    assert row["payment"]["last_policy_decision"] is None
    prov = c.get(f"/workspace/agreements/{agreement.id}", headers=h("maya")).json()
    assert prov["perspective"] == "provider"
    assert c.get("/workspace/agreements", headers=h("eve")).json() == []

    c.get(f"/payments/authorizations/{auth_id}/policy", headers=h("bob"))
    r = c.post(
        f"/verification/runs/{run.id}/manual-reviews",
        json={
            "criterion_id": manual["criterion_id"],
            "decision": "satisfied",
            "rationale": "SCRIPTED DEMO DECISION: test fixture, no human review.",
            "evidence_snapshot_sha256": rv["evidence_snapshot_sha256"],
        },
        headers=h("rita"),
    )
    assert r.status_code == 201
    p = c.get(f"/payments/authorizations/{auth_id}/policy", headers=h("bob")).json()
    a = c.post(
        f"/payments/authorizations/{auth_id}/capture-approvals",
        json={"inputs_sha256": p["inputs_sha256"]},
        headers=h("bob"),
    ).json()
    approvals = c.get(
        f"/payments/authorizations/{auth_id}/capture-approvals", headers=h("alice")
    ).json()
    assert [x["id"] for x in approvals] == [a["approval_id"]]
    assert approvals[0]["approver_email"] == "bob@acme.demo"
    assert approvals[0]["consumed_by_capture_id"] is None
    assert (
        c.get(f"/payments/authorizations/{auth_id}/capture-approvals", headers=h("eve")).status_code
        == 404
    )

    row = next(
        x
        for x in c.get("/workspace/agreements", headers=h("alice")).json()
        if x["id"] == agreement.id
    )
    assert row["payment"]["last_policy_decision"] == "eligible_for_approval"
    assert row["latest_completed_run"]["awaiting_review"] == 0

    # Ledger: only persisted events of visible agreements; previews hidden by default.
    led = c.get(f"/workspace/ledger?agreement_id={agreement.id}", headers=h("alice")).json()
    types = [x["event_type"] for x in led["rows"]]
    assert "agreement.version.approved_and_locked" in types
    assert "manual_review.satisfied" in types and "capture.approved" in types
    assert not any(t.startswith("policy.preview.") for t in types)
    review = next(x for x in led["rows"] if x["event_type"] == "manual_review.satisfied")
    assert review["provenance"] == "scripted" and review["actor_type"] == "human_account"
    assert review["demo_account"] is True and review["criterion_key"] == "website_accessible"
    authz = next(x for x in led["rows"] if x["event_type"] == "payment.authorization.created")
    assert authz["amount"] == "500.00" and authz["transaction_ref"].startswith("SIM-")
    assert authz["payment_environment"] == "simulated"
    assert led["total"] == len(led["rows"]) and "not tamper-proof" in led["chain_note"]
    with_previews = c.get(
        f"/workspace/ledger?agreement_id={agreement.id}&include_previews=true", headers=h("alice")
    ).json()
    assert with_previews["total"] > led["total"]
    only_reviews = c.get(
        f"/workspace/ledger?agreement_id={agreement.id}&category=review", headers=h("alice")
    ).json()
    assert {x["category"] for x in only_reviews["rows"]} == {"review"}
    paged = c.get(
        f"/workspace/ledger?agreement_id={agreement.id}&limit=2&offset=1", headers=h("alice")
    ).json()
    assert len(paged["rows"]) == 2 and paged["rows"][0]["seq"] == led["rows"][1]["seq"]
    assert (
        c.get(f"/workspace/ledger?agreement_id={agreement.id}", headers=h("eve")).status_code == 404
    )
    assert c.get("/workspace/ledger", headers=h("eve")).json()["rows"] == []


def test_suggestion_opt_in_over_http(http, settings):
    c, seed, h, _, ids = http
    from outcomeguard.api import routes_ai

    provider = ScriptedProvider(
        [
            draft_json(
                [
                    crit("mobile_layout", "mobile_viewport", "mobile-friendly landing page"),
                    crit("site_reachable", "http_availability", "landing page", origin="suggested"),
                ]
            )
        ]
        * 2,
        model="claude-sonnet-5-5",
    )
    c.app.dependency_overrides[routes_ai.get_ai_provider] = lambda: provider
    try:
        req = "Build a mobile-friendly landing page for $500."
        body = {
            "merchant_account_id": ids.merchant.id,
            "title": "t",
            "amount": "500.00",
            "delivery_deadline": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
        }
        keys = []
        for accept in ([], ["site_reachable"]):
            d = c.post(
                "/agreements/drafts",
                json={"buyer_organization_id": ids.buyer_org_id, "requirements_text": req},
                headers=h("alice"),
            ).json()
            assert [s["key"] for s in d["review"]["suggestions"]] == ["site_reachable"]
            r = c.post(
                f"/agreements/drafts/{d['id']}/accept",
                json={**body, "accept_suggestions": accept},
                headers=h("alice"),
            )
            assert r.status_code == 201, r.text
            a = c.get(f"/agreements/{r.json()['agreement_id']}", headers=h("alice")).json()
            keys.append([x["key"] for x in a["versions"][0]["criteria"]])
        assert keys == [["mobile_layout"], ["mobile_layout", "site_reachable"]]
    finally:
        c.app.dependency_overrides.clear()
