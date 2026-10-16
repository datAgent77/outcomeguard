"""P08 over HTTP with real sessions; rehearsal gateway → every payment is SIMULATED.
Covers RBAC/isolation at the API, Idempotency-Key, the refund and void flows, the ledger
trail and the PayPal VOIDED webhook path."""

from types import SimpleNamespace

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

    def who(name):
        email = next(e for e in seed["users"] if e.startswith(name))
        return load_principal(session, seed["users"][email])

    ids = SimpleNamespace(
        buyer_org_id=seed["buyer_organization_id"],
        merchant=SimpleNamespace(id=seed["merchant_account_id"]),
    )
    return c, h, who, ids


def authorized(c, h, agreement_id):
    order = c.post(f"/agreements/{agreement_id}/payments", headers=h("alice")).json()
    c.post(f"/rehearsal/approve/{order['paypal_order_id']}")
    return c.post(f"/payments/orders/{order['id']}/authorize", headers=h("bob")).json()[
        "authorization"
    ]["id"]


def capture(c, h, session, who, agreement, auth_id, tmp_path):
    run = completed_run(session, who, agreement, tmp_path, form_ok=True)
    rv = c.get(f"/verification/runs/{run.id}", headers=h("rita")).json()
    crit = next(r for r in rv["results"] if r["criterion_key"] == "website_accessible")[
        "criterion_id"
    ]
    r = c.post(
        f"/verification/runs/{run.id}/manual-reviews",
        json={
            "criterion_id": crit,
            "decision": "satisfied",
            "rationale": "SCRIPTED DEMO DECISION: test",
            "evidence_snapshot_sha256": rv["evidence_snapshot_sha256"],
        },
        headers=h("rita"),
    )
    assert r.status_code == 201, r.text
    p = c.get(f"/payments/authorizations/{auth_id}/policy", headers=h("bob")).json()
    a = c.post(
        f"/payments/authorizations/{auth_id}/capture-approvals",
        json={"inputs_sha256": p["inputs_sha256"]},
        headers=h("bob"),
    ).json()
    cap = c.post(
        f"/payments/authorizations/{auth_id}/captures",
        json={"approval_id": a["approval_id"]},
        headers=h("bob"),
    ).json()
    assert cap["status"] == "completed" and cap["environment"] == "simulated"
    return run


def test_refund_flow_over_http(http, session, tmp_path):
    c, h, who, ids = http
    agreement = locked_agreement_with_manual(session, ids, who)
    auth_id = authorized(c, h, agreement.id)
    run = capture(c, h, session, who, agreement, auth_id, tmp_path)

    body = {
        "reason_category": "defect_after_acceptance",
        "description": "Form stopped working after launch.",
        "requested_remedy": "partial_refund",
        "requested_amount": "100.00",
    }
    assert (
        c.post(f"/agreements/{agreement.id}/resolutions", json=body, headers=h("maya")).status_code
        == 403
    )
    assert (
        c.post(f"/agreements/{agreement.id}/resolutions", json=body, headers=h("eve")).status_code
        == 404
    )
    case = c.post(f"/agreements/{agreement.id}/resolutions", json=body, headers=h("alice")).json()
    assert case["status"] == "open" and case["your_side"] == "buyer"
    assert case["evidence_snapshot"]["verification_run"]["id"] == run.id
    assert case["financials"] == {
        **case["financials"],
        "captured": "500.00",
        "remaining": "500.00",
        "refunded": "0.00",
    }
    cid = case["id"]
    assert c.get(f"/resolutions/{cid}", headers=h("eve")).status_code == 404

    case = c.post(
        f"/resolutions/{cid}/messages",
        json={"body": "We offer a partial refund."},
        headers=h("maya"),
    ).json()
    assert case["status"] == "under_review" and case["your_side"] == "provider"
    case = c.post(
        f"/resolutions/{cid}/proposals",
        json={
            "expected_revision": case["revision"],
            "remedy": "partial_refund",
            "amount": "100.00",
            "rationale": "Refund 100 for the broken form",
            "approve": True,
        },
        headers=h("maya"),
    ).json()
    p = case["proposals"][-1]
    assert p["status"] == "open" and [a["side"] for a in p["approvals"]] == ["provider"]

    pol = c.get(f"/resolutions/{cid}/policy", headers=h("bob")).json()
    assert pol["decision"] == "blocked" and "APPROVAL_MISSING" in [
        r["code"] for r in pol["reasons"]
    ]
    r = c.post(f"/resolutions/{cid}/refunds", json={"proposal_id": p["id"]}, headers=h("bob"))
    assert r.status_code == 422  # Idempotency-Key is mandatory

    case = c.post(
        f"/resolutions/{cid}/proposals/{p['id']}/approve",
        json={"content_sha256": p["content_sha256"]},
        headers=h("bob"),
    ).json()
    assert case["status"] == "agreed"
    pol = c.get(f"/resolutions/{cid}/policy", headers=h("bob")).json()
    assert pol["decision"] == "allowed" and pol["figures"]["remaining_minor"] == 50000

    key = {**h("bob"), "Idempotency-Key": "refund-http-0001"}
    assert (
        c.post(
            f"/resolutions/{cid}/refunds",
            json={"proposal_id": p["id"]},
            headers={**h("rita"), "Idempotency-Key": "refund-http-rita"},
        ).status_code
        == 409
    )
    r1 = c.post(f"/resolutions/{cid}/refunds", json={"proposal_id": p["id"]}, headers=key).json()
    r2 = c.post(f"/resolutions/{cid}/refunds", json={"proposal_id": p["id"]}, headers=key).json()
    assert r1["id"] == r2["id"] and r1["status"] == "completed" and r1["environment"] == "simulated"
    case = c.get(f"/resolutions/{cid}", headers=h("alice")).json()
    assert case["status"] == "resolved"
    assert (
        case["financials"]["refunded"] == "100.00" and case["financials"]["remaining"] == "400.00"
    )
    assert [x["id"] for x in c.get(f"/resolutions/{cid}/refunds", headers=h("maya")).json()] == [
        r1["id"]
    ]

    led = c.get(f"/workspace/ledger?agreement_id={agreement.id}", headers=h("alice")).json()
    types = [x["event_type"] for x in led["rows"]]
    for t in (
        "resolution.opened",
        "resolution.proposal_submitted",
        "resolution.proposal_approved",
        "policy.refund.allowed",
        "payment.refund.requested",
        "payment.refund.completed",
        "resolution.refund_completed",
    ):
        assert t in types, t
    assert not any(t.startswith("policy.preview.") for t in types)
    refund_row = next(x for x in led["rows"] if x["event_type"] == "payment.refund.completed")
    assert refund_row["payment_environment"] == "simulated" and refund_row["amount"] == "100.00"
    opened = next(x for x in led["rows"] if x["event_type"] == "resolution.opened")
    assert opened["resolution_case_id"] == cid
    ov = next(
        x
        for x in c.get("/workspace/agreements", headers=h("alice")).json()
        if x["id"] == agreement.id
    )
    assert ov["payment"]["refunded"] == "100.00" and ov["open_resolution"] is None


def test_void_flow_over_http(http, session):
    c, h, who, ids = http
    agreement = locked_agreement_with_manual(session, ids, who)
    auth_id = authorized(c, h, agreement.id)
    pol = c.get(f"/payments/authorizations/{auth_id}/void-policy", headers=h("bob")).json()
    assert pol["decision"] == "allowed"
    r = c.post(
        f"/payments/authorizations/{auth_id}/void",
        json={"reason": "provider never started"},
        headers=h("maya"),
    )
    assert r.status_code == 409 and "ACTOR_NOT_AUTHORIZED" in [
        x["code"] for x in r.json()["reasons"]
    ]
    assert (
        c.post(
            f"/payments/authorizations/{auth_id}/void",
            json={"reason": "outsider"},
            headers=h("eve"),
        ).status_code
        == 404
    )
    assert c.get(f"/payments/authorizations/{auth_id}/void-status", headers=h("bob")).json() is None
    v1 = c.post(
        f"/payments/authorizations/{auth_id}/void",
        json={"reason": "project cancelled"},
        headers=h("bob"),
    ).json()
    v2 = c.post(
        f"/payments/authorizations/{auth_id}/void",
        json={"reason": "project cancelled"},
        headers=h("bob"),
    ).json()
    assert v1["status"] == "completed" and v1["id"] == v2["id"]
    assert (
        c.get(f"/payments/authorizations/{auth_id}/void-status", headers=h("maya")).json()["status"]
        == "completed"
    )
    ov = next(
        x
        for x in c.get("/workspace/agreements", headers=h("alice")).json()
        if x["id"] == agreement.id
    )
    assert (
        ov["payment"]["authorization_status"] == "voided"
        and ov["payment"]["void_status"] == "completed"
    )
    led = c.get(f"/workspace/ledger?agreement_id={agreement.id}", headers=h("alice")).json()
    types = [x["event_type"] for x in led["rows"]]
    assert "payment.void.requested" in types and "payment.void.completed" in types


def test_voided_webhook_settles_an_uncertain_void(session, svc, gateway, ids, who):
    from sqlalchemy import select

    from outcomeguard.models import AuthorizationVoid, VoidStatus
    from outcomeguard.paypal.gateway import PayPalUncertainError
    from outcomeguard.services.payments import PaymentUncertain
    from outcomeguard.services.resolution import Resolution
    from outcomeguard.services.webhooks import handle_webhook
    from tests.govhelpers import authorized_payment

    agreement = locked_agreement_with_manual(session, ids, who)
    auth = authorized_payment(session, svc, gateway, agreement)
    gateway.fail_after["void_authorization"] = PayPalUncertainError("lost")
    with pytest.raises(PaymentUncertain):
        Resolution(session=session, payments=svc).void(who("bob"), auth.id, reason="cancel")
    event = {
        "id": "WH-VOID-1",
        "event_type": "PAYMENT.AUTHORIZATION.VOIDED",
        "resource": {"id": auth.paypal_authorization_id},
    }
    result = handle_webhook(svc, {"paypal-transmission-id": "t"}, event)
    assert result.status == "processed" and "void reconciled" in result.detail
    session.expire_all()
    assert session.scalar(select(AuthorizationVoid)).status is VoidStatus.COMPLETED
    assert gateway.calls["void_authorization"] == 1
