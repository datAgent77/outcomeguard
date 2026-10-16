from fastapi.testclient import TestClient
from sqlalchemy import select, text

from outcomeguard.models import AuditEvent, CaptureStatus, PaymentCapture, WebhookEvent
from outcomeguard.models.audit import verify_chain
from outcomeguard.services.webhooks import handle_webhook


def event(eid: str, etype: str, rid: str) -> dict:
    return {"id": eid, "event_type": etype, "resource": {"id": rid, "status": "COMPLETED"}}


# ----------------------------------------------------------------- webhooks


def test_unverified_webhook_is_recorded_but_not_processed(svc, capture, gateway):
    gateway.webhook_verification = "FAILURE"
    res = handle_webhook(
        svc, {}, event("WH-1", "PAYMENT.CAPTURE.COMPLETED", capture.paypal_capture_id)
    )
    assert res.status == "rejected"
    assert gateway.calls["get_capture"] == 0
    row = svc.session.scalar(select(WebhookEvent))
    assert row.verification_status == "FAILURE" and row.processed_at is None


def test_replayed_webhook_processed_once(svc, capture, gateway):
    ev = event("WH-2", "PAYMENT.CAPTURE.COMPLETED", capture.paypal_capture_id)
    assert handle_webhook(svc, {}, ev).status == "processed"
    assert handle_webhook(svc, {}, ev).status == "duplicate"
    assert gateway.calls["get_capture"] == 1


def test_webhook_completes_pending_capture_from_paypal_state(svc, authorization, gateway):
    gateway.override["capture_authorization"] = lambda r: {**r, "status": "PENDING"}
    cap = svc.capture_authorization(authorization.id, actor="approver")
    assert cap.status is CaptureStatus.PENDING
    # PayPal later completes it; the webhook payload is not trusted, the resource is re-read.
    gateway.set_capture_status(cap.paypal_capture_id, "COMPLETED")
    handle_webhook(svc, {}, event("WH-3", "PAYMENT.CAPTURE.COMPLETED", cap.paypal_capture_id))
    assert svc._get(PaymentCapture, cap.id).status is CaptureStatus.COMPLETED


def test_out_of_order_stale_event_cannot_regress_completed(svc, capture, gateway):
    gateway.set_capture_status(capture.paypal_capture_id, "PENDING")  # stale read
    handle_webhook(svc, {}, event("WH-4", "PAYMENT.CAPTURE.PENDING", capture.paypal_capture_id))
    assert svc._get(PaymentCapture, capture.id).status is CaptureStatus.COMPLETED


def test_unknown_capture_is_unmatched(svc, gateway):
    res = handle_webhook(svc, {}, event("WH-5", "PAYMENT.CAPTURE.COMPLETED", "NOT-OURS"))
    assert res.status == "unmatched"


# -------------------------------------------------------------------- audit


def test_audit_chain_intact_and_detects_tampering(svc, capture, session):
    ok, _ = verify_chain(session)
    assert ok
    actions = session.scalars(select(AuditEvent.action).order_by(AuditEvent.seq)).all()
    assert "payment.authorization.created" in actions and "payment.capture.completed" in actions
    session.execute(text("UPDATE audit_events SET actor = 'mallory' WHERE seq = 2"))
    session.commit()
    ok, bad = verify_chain(session)
    assert not ok and bad == 2


def test_audit_redacts_secrets(session):
    from outcomeguard.models.audit import record_audit

    ev = record_audit(
        session,
        actor="t",
        action="x",
        entity_type="t",
        entity_id="1",
        data={"client_secret": "s3cr3t", "nested": {"access_token": "abc"}},
    )
    session.commit()
    assert "s3cr3t" not in str(ev.data) and "abc" not in str(ev.data)


# ---------------------------------------------------------------------- API


def client() -> TestClient:
    from outcomeguard.main import create_app

    return TestClient(create_app())


def test_health_and_ready(make_session):
    c = client()
    assert c.get("/health").json() == {"status": "ok"}
    body = c.get("/ready").json()
    assert body["status"] == "ready"
    assert body["config"]["payment_mode"] == "rehearsal"
    assert body["config"]["paypal_environment"] == "none (simulated)"
    assert "secret" not in str(body).lower()


def test_operator_endpoints_require_token(make_session):
    c = client()
    assert c.get("/dev/payments/orders").status_code == 401
    assert c.get("/dev/payments/orders", headers={"X-Operator-Token": "wrong"}).status_code == 401


def test_rehearsal_flow_over_http_is_labeled_simulated(make_session):
    c = client()
    h = {"X-Operator-Token": "test-operator-token"}
    order = c.post("/dev/payments/orders", json={"amount": "500.00"}, headers=h).json()
    assert order["simulated"] is True and order["paypal_order_id"].startswith("SIM-")
    # Not approved yet → authorize blocked.
    r = c.post(f"/dev/payments/orders/{order['id']}/authorize", headers=h)
    assert r.status_code == 409 and r.json()["code"] == "ORDER_NOT_APPROVED"
    # Simulated buyer approval, then the return page re-reads status server-side.
    r = c.post(f"/rehearsal/approve/{order['paypal_order_id']}", follow_redirects=True)
    assert "SIMULATED" in r.text and "APPROVED" in r.text
    o = c.post(f"/dev/payments/orders/{order['id']}/authorize", headers=h).json()
    cap = c.post(
        f"/dev/payments/authorizations/{o['authorization']['id']}/capture", json={}, headers=h
    ).json()
    assert cap["status"] == "completed"
    ref = c.post(
        f"/dev/payments/captures/{cap['id']}/refunds",
        json={"amount": "50.00", "idempotency_key": "demo-refund-1"},
        headers=h,
    ).json()
    assert ref["status"] == "completed" and ref["environment"] == "simulated"
    over = c.post(
        f"/dev/payments/captures/{cap['id']}/refunds",
        json={"amount": "450.01", "idempotency_key": "demo-refund-2"},
        headers=h,
    )
    assert over.status_code == 422 and over.json()["code"] == "REFUND_EXCEEDS_BALANCE"
    assert c.get("/dev/payments/audit/verify", headers=h).json()["chain_intact"] is True


def test_webhook_endpoint_rejects_garbage(make_session):
    c = client()
    assert c.post("/webhooks/paypal", content=b"not json").status_code == 400


def test_access_log_redacts_paypal_return_query():
    import logging

    from outcomeguard.logs import RedactPayPalQuery

    rec = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:1", "GET", "/paypal/return?token=ABC&PayerID=XYZ", "1.1", 200),
        None,
    )
    RedactPayPalQuery().filter(rec)
    msg = rec.getMessage()
    assert "ABC" not in msg and "XYZ" not in msg and "/paypal/return?[REDACTED]" in msg
