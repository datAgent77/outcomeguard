"""PayPal webhook intake.

1. Verify the signature with PayPal (POST /v1/notifications/verify-webhook-signature).
2. Record the delivery; `paypal_event_id` is unique, so a replayed delivery is a no-op.
3. For verified events, do NOT trust the payload's state: re-read the resource from
   PayPal and reconcile. That makes processing order-independent — an out-of-order or
   stale event can only ever apply PayPal's *current* state, and settle functions refuse
   to regress a terminal status.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from outcomeguard.logs import redact
from outcomeguard.models import (
    PaymentAuthorization,
    PaymentCapture,
    PaymentRefund,
    WebhookEvent,
)
from outcomeguard.models.audit import record_audit
from outcomeguard.models.base import utcnow
from outcomeguard.paypal.gateway import Json, PayPalError, PayPalNotConfiguredError
from outcomeguard.services.payments import PaymentService

log = logging.getLogger(__name__)

HANDLED = {
    "PAYMENT.AUTHORIZATION.CREATED",
    "PAYMENT.AUTHORIZATION.VOIDED",
    "PAYMENT.CAPTURE.COMPLETED",
    "PAYMENT.CAPTURE.PENDING",
    "PAYMENT.CAPTURE.DENIED",
    "PAYMENT.CAPTURE.DECLINED",
    "PAYMENT.CAPTURE.REFUNDED",
    "PAYMENT.CAPTURE.REVERSED",
}


@dataclass
class WebhookResult:
    status: str  # processed | duplicate | rejected | ignored | unmatched
    detail: str = ""


def handle_webhook(svc: PaymentService, headers: dict[str, str], event: Json) -> WebhookResult:
    session: Session = svc.session
    event_id = event.get("id")
    if not isinstance(event_id, str) or not event_id:
        return WebhookResult("rejected", "missing event id")

    try:
        verification = svc.gateway.verify_webhook_signature(headers, event)
    except PayPalNotConfiguredError:
        verification = "SKIPPED"
    except PayPalError as exc:
        log.warning("webhook verification error", extra={"data": {"error": str(exc)}})
        verification = "ERROR"

    resource = event.get("resource") or {}
    row = WebhookEvent(
        paypal_event_id=event_id,
        event_type=str(event.get("event_type", ""))[:80],
        resource_id=str(resource.get("id", ""))[:64] or None,
        transmission_id={k.lower(): v for k, v in headers.items()}.get("paypal-transmission-id"),
        verification_status=verification,
        received_at=utcnow(),
        payload=redact(event),
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return WebhookResult("duplicate", "event already received")

    if verification != "SUCCESS":
        row.processing_result = f"not processed: signature {verification}"
        session.commit()
        return WebhookResult("rejected", f"signature {verification}")

    result = _process(svc, row)
    stored = session.get(WebhookEvent, row.id)
    assert stored is not None
    row = stored
    row.processed_at = utcnow()
    row.processing_result = f"{result.status}: {result.detail}"[:200]
    record_audit(
        session,
        actor="paypal-webhook",
        action="webhook.processed",
        entity_type="webhook_event",
        entity_id=row.id,
        data={"event_type": row.event_type, "result": row.processing_result},
    )
    session.commit()
    return result


def _process(svc: PaymentService, row: WebhookEvent) -> WebhookResult:
    if row.event_type not in HANDLED:
        return WebhookResult("ignored", row.event_type)
    s = svc.session
    rid = row.resource_id
    if row.event_type.startswith("PAYMENT.AUTHORIZATION."):
        auth = s.scalar(
            select(PaymentAuthorization).where(PaymentAuthorization.paypal_authorization_id == rid)
        )
        if not auth:
            return WebhookResult("unmatched", f"authorization {rid}")
        svc.refresh_authorization(auth.id)
        return WebhookResult("processed", f"authorization {rid} refreshed")

    if row.event_type == "PAYMENT.CAPTURE.REFUNDED":
        # resource is the refund; it links to its capture.
        refund = s.scalar(select(PaymentRefund).where(PaymentRefund.paypal_refund_id == rid))
        if refund:
            svc.reconcile_refund(refund.id)
            return WebhookResult("processed", f"refund {rid} reconciled")
        return WebhookResult("unmatched", f"refund {rid} not initiated by OutcomeGuard")

    capture = s.scalar(select(PaymentCapture).where(PaymentCapture.paypal_capture_id == rid))
    if not capture:
        return WebhookResult("unmatched", f"capture {rid}")
    svc.reconcile_capture(capture.id)
    return WebhookResult("processed", f"capture {rid} reconciled")
