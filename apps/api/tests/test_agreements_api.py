"""Agreement API over HTTP: authentication, RBAC responses, full lifecycle."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

OP = {"X-Operator-Token": "test-operator-token"}
DEMO_PW = "correct horse battery staple"


@pytest.fixture
def api(make_session):
    from outcomeguard.main import create_app

    c = TestClient(create_app())
    seed = c.post("/dev/seed", json={"password": DEMO_PW}, headers=OP).json()

    def as_(email: str) -> dict:
        r = c.post("/auth/login", json={"email": email, "password": DEMO_PW})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['token']}"}

    return c, seed, as_


def create_body(seed) -> dict:
    return {
        "buyer_organization_id": seed["buyer_organization_id"],
        "merchant_account_id": seed["merchant_account_id"],
        "title": "Spring campaign landing page",
        "requirements_text": "Responsive landing page, working contact form, Lighthouse >= 90",
        "amount": "500.00",
        "delivery_deadline": (datetime.now(UTC) + timedelta(days=10)).isoformat(),
    }


def test_requires_authentication(api):
    c, _, _ = api
    assert c.get("/agreements").status_code == 401
    assert c.get("/agreements", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_seed_is_operator_gated(api):
    c, _, _ = api
    assert c.post("/dev/seed", json={"password": DEMO_PW}).status_code == 401
    assert c.post("/dev/session", json={"email": "alice@acme.demo"}).status_code in (404, 405)


def test_full_lifecycle_over_http(api):
    c, seed, as_ = api
    alice, bob, maya, eve = (
        as_(e)
        for e in ("alice@acme.demo", "bob@acme.demo", "maya@northwind.demo", "eve@globex.demo")
    )
    r = c.post("/agreements", json=create_body(seed), headers=alice)
    assert r.status_code == 201, r.text
    agr = r.json()
    aid, ver = agr["id"], agr["versions"][0]
    assert ver["status"] == "draft" and len(ver["criteria"]) == 4 and ver["amount"] == "500.00"

    # client cannot sneak in status/amount via unknown fields
    r = c.patch(
        f"/agreements/{aid}/versions/1",
        json={"expected_revision": ver["revision"], "status": "locked"},
        headers=alice,
    )
    assert r.status_code == 422

    r = c.patch(
        f"/agreements/{aid}/versions/1",
        json={"expected_revision": ver["revision"], "amount": "450.00"},
        headers=alice,
    )
    ver = r.json()["versions"][0]
    assert ver["amount"] == "450.00"

    assert (
        c.patch(
            f"/agreements/{aid}/versions/1",
            json={"expected_revision": ver["revision"], "title": "x"},
            headers=maya,
        ).status_code
        == 403
    )
    assert c.get(f"/agreements/{aid}", headers=eve).status_code == 404
    assert c.get("/agreements", headers=eve).json() == []

    ver = c.post(
        f"/agreements/{aid}/versions/1/propose",
        json={"expected_revision": ver["revision"]},
        headers=alice,
    ).json()["versions"][0]
    assert ver["status"] == "proposed" and len(ver["content_hash"]) == 64

    r = c.post(
        f"/agreements/{aid}/versions/1/approve",
        json={"content_hash": ver["content_hash"]},
        headers=maya,
    )
    assert r.status_code == 403
    r = c.post(
        f"/agreements/{aid}/versions/1/approve",
        json={"content_hash": ver["content_hash"], "note": "Looks right"},
        headers=bob,
    )
    assert r.status_code == 200, r.text
    agr = r.json()
    ver = agr["versions"][0]
    assert agr["status"] == "active" and ver["status"] == "locked"
    assert ver["approvals"][0]["approver_role"] == "buyer_approver"

    r = c.post(
        f"/agreements/{aid}/versions/1/approve",
        json={"content_hash": ver["content_hash"]},
        headers=bob,
    )
    assert r.status_code == 409 and r.json()["code"] == "ALREADY_APPROVED"
    r = c.put(
        f"/agreements/{aid}/versions/1/criteria",
        json={"expected_revision": ver["revision"], "criteria": []},
        headers=alice,
    )
    assert r.status_code == 409 and r.json()["code"] == "VERSION_LOCKED"

    r = c.post(f"/agreements/{aid}/versions", headers=alice)
    assert r.status_code == 201 and r.json()["versions"][1]["status"] == "draft"


def test_custom_criteria_validated_over_http(api):
    c, seed, as_ = api
    body = create_body(seed) | {
        "criteria": [
            {
                "key": "fast",
                "title": "Fast",
                "spec": {"kind": "lighthouse_performance", "min_score": 101},
            }
        ]
    }
    r = c.post("/agreements", json=body, headers=as_("alice@acme.demo"))
    assert r.status_code == 422
