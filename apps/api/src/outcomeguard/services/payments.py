"""Payment lifecycle: order → authorization → capture → refund, plus reconciliation.

Rules this module enforces (see docs/PAYPAL_INTEGRATION.md):

* The browser is never trusted. Approval is established by reading the order from
  PayPal; amounts, currency and payee are always taken from our database and checked
  against what PayPal returns.
* Money-moving calls (authorize, capture, refund) follow reserve → call → settle:
    1. lock the parent row (SELECT … FOR UPDATE), check the rules, write a REQUESTED
       row with a deterministic PayPal-Request-Id, commit;
    2. call PayPal outside any transaction;
    3. lock again and apply the actual PayPal response.
  A crash or timeout between 1 and 3 leaves an UNKNOWN/REQUESTED row that blocks any
  new attempt until `reconcile_*` replays the same PayPal-Request-Id (PayPal returns
  the original result instead of moving money twice).
* Nothing is marked successful unless PayPal's response says so.

This layer knows nothing about agreements or AI. Whether a capture is *allowed* is
decided by the policy engine (P06), which calls `capture_authorization` only after its
own checks; this layer re-checks every payment-level invariant regardless.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from outcomeguard.config import Settings
from outcomeguard.logs import redact
from outcomeguard.models import (
    AuthorizationStatus,
    CaptureStatus,
    MerchantAccount,
    OrderStatus,
    PaymentAuthorization,
    PaymentCapture,
    PaymentEnvironment,
    PaymentOrder,
    PaymentRefund,
    RefundStatus,
)
from outcomeguard.models.audit import record_audit
from outcomeguard.models.base import utcnow
from outcomeguard.models.payments import REFUND_HOLDS_BALANCE
from outcomeguard.money import Money, MoneyError
from outcomeguard.paypal.gateway import (
    Json,
    PayPalAPIError,
    PayPalGateway,
    PayPalUncertainError,
    link,
)

log = logging.getLogger(__name__)

HONOR_PERIOD = timedelta(days=3)
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class PaymentRuleViolation(Exception):
    """A payment invariant blocked the operation. `code` is stable and machine-readable."""

    def __init__(self, code: str, message: str, *, http_status: int = 409) -> None:
        self.code = code
        self.message = message
        self.http_status = http_status
        super().__init__(f"{code}: {message}")


class PaymentUncertain(Exception):
    """PayPal's outcome is unknown; the record is UNKNOWN and must be reconciled."""

    def __init__(self, entity: str, entity_id: str) -> None:
        self.entity = entity
        self.entity_id = entity_id
        super().__init__(f"{entity} {entity_id} outcome unknown — reconcile before retrying")


@dataclass
class PaymentService:
    session: Session
    gateway: PayPalGateway
    settings: Settings

    @property
    def environment(self) -> PaymentEnvironment:
        return (
            PaymentEnvironment.SIMULATED if self.gateway.simulated else PaymentEnvironment.SANDBOX
        )

    # ------------------------------------------------------------------ orders

    def create_order(
        self, *, merchant: MerchantAccount, amount: Money, description: str, actor: str
    ) -> PaymentOrder:
        if merchant.environment is not self.environment:
            raise PaymentRuleViolation(
                "MERCHANT_ENVIRONMENT_MISMATCH",
                f"merchant is {merchant.environment.value}, gateway is {self.environment.value}",
            )
        if amount.minor <= 0:
            raise PaymentRuleViolation("INVALID_AMOUNT", "amount must be positive", http_status=422)
        reference = f"og_{uuid.uuid4().hex[:24]}"
        order = PaymentOrder(
            merchant_account_id=merchant.id,
            environment=self.environment,
            reference=reference,
            create_request_id=f"og-order-{reference}",
            amount_minor=amount.minor,
            currency=amount.currency,
            description=description[:127],
            status=OrderStatus.CREATED,
        )
        self.session.add(order)
        self.session.flush()
        record_audit(
            self.session,
            actor=actor,
            action="payment.order.requested",
            entity_type="payment_order",
            entity_id=order.id,
            data={"amount": str(amount), "environment": self.environment.value},
        )
        self.session.commit()
        self._send_create_order(order)
        return order

    def _order_body(self, order: PaymentOrder) -> Json:
        amount = Money(order.amount_minor, order.currency)
        return {
            "intent": "AUTHORIZE",
            "purchase_units": [
                {
                    "reference_id": order.reference,
                    "custom_id": order.reference,
                    "invoice_id": order.reference,
                    "description": order.description,
                    "amount": amount.to_paypal(),
                }
            ],
            "payment_source": {
                "paypal": {
                    "experience_context": {
                        "brand_name": "OutcomeGuard",
                        "shipping_preference": "NO_SHIPPING",
                        # Funds are only held at this point, not taken.
                        "user_action": "CONTINUE",
                        "return_url": f"{self.settings.public_api_url}/paypal/return",
                        "cancel_url": f"{self.settings.public_api_url}/paypal/cancel",
                    }
                }
            },
        }

    def _send_create_order(self, order: PaymentOrder) -> None:
        try:
            resp = self.gateway.create_order(
                self._order_body(order), request_id=order.create_request_id
            )
        except PayPalAPIError as exc:
            order.status = OrderStatus.FAILED
            order.last_paypal_response = {"error": exc.to_record()}
            record_audit(
                self.session,
                actor="system",
                action="payment.order.failed",
                entity_type="payment_order",
                entity_id=order.id,
                data=exc.to_record(),
            )
            self.session.commit()
            raise
        except PayPalUncertainError:
            # Row stays CREATED with no paypal_order_id; retry_create_order() replays
            # the same PayPal-Request-Id.
            self.session.rollback()
            raise PaymentUncertain("payment_order", order.id) from None

        self._check_amount(order, resp["purchase_units"][0]["amount"], "order")
        order.paypal_order_id = resp["id"]
        order.paypal_status = resp.get("status")
        order.approval_url = link(resp, "payer-action") or link(resp, "approve")
        order.last_paypal_response = redact(resp)
        record_audit(
            self.session,
            actor="system",
            action="payment.order.created",
            entity_type="payment_order",
            entity_id=order.id,
            data={"paypal_order_id": order.paypal_order_id, "paypal_status": order.paypal_status},
        )
        self.session.commit()

    def retry_create_order(self, order: PaymentOrder) -> PaymentOrder:
        if order.paypal_order_id or order.status is not OrderStatus.CREATED:
            return order
        self._send_create_order(order)
        return order

    def sync_order(self, order: PaymentOrder, *, actor: str = "system") -> PaymentOrder:
        """Read the order from PayPal and record whether the buyer approved it."""
        if not order.paypal_order_id:
            raise PaymentRuleViolation("ORDER_NOT_CREATED", "order has no PayPal id yet")
        resp = self.gateway.get_order(order.paypal_order_id)
        self._check_order_identity(order, resp)
        previous = order.status
        order.paypal_status = resp.get("status")
        order.last_paypal_response = redact(resp)
        payer_id = (resp.get("payer") or {}).get("payer_id")
        if payer_id:
            order.payer_id = payer_id
        if order.status is OrderStatus.CREATED and order.paypal_status == "APPROVED":
            order.status = OrderStatus.APPROVED
        if order.status is not previous:
            record_audit(
                self.session,
                actor=actor,
                action=f"payment.order.{order.status.value}",
                entity_type="payment_order",
                entity_id=order.id,
                data={"paypal_status": order.paypal_status},
            )
        self.session.commit()
        return order

    def mark_cancelled(self, order: PaymentOrder) -> PaymentOrder:
        if order.status in (OrderStatus.CREATED, OrderStatus.APPROVED) and not order.authorization:
            order.status = OrderStatus.CANCELLED
            record_audit(
                self.session,
                actor="buyer",
                action="payment.order.cancelled",
                entity_type="payment_order",
                entity_id=order.id,
                data={},
            )
            self.session.commit()
        return order

    # ----------------------------------------------------------- authorization

    def authorize_order(self, order_id: str, *, actor: str) -> PaymentAuthorization:
        order = self._lock(PaymentOrder, order_id)
        if order.authorization:
            self.session.commit()
            return order.authorization
        if order.status not in (OrderStatus.CREATED, OrderStatus.APPROVED):
            raise PaymentRuleViolation("ORDER_NOT_AUTHORIZABLE", f"order is {order.status.value}")
        self.session.commit()  # release the lock during network I/O

        current = self.gateway.get_order(_require(order.paypal_order_id, "paypal_order_id"))
        self._check_order_identity(order, current)
        status = current.get("status")
        if status == "APPROVED":
            try:
                resp = self.gateway.authorize_order(
                    order.paypal_order_id, request_id=f"og-auth-{order.id}"
                )
            except PayPalUncertainError:
                raise PaymentUncertain("payment_order", order.id) from None
        elif status == "COMPLETED":
            # Authorized at PayPal but we never recorded it (crash/timeout): adopt it.
            resp = current
        else:
            raise PaymentRuleViolation(
                "ORDER_NOT_APPROVED",
                f"PayPal reports order status {status}; the buyer has not approved it",
            )

        auths = resp["purchase_units"][0].get("payments", {}).get("authorizations", [])
        if len(auths) != 1:
            raise PaymentRuleViolation(
                "UNEXPECTED_AUTHORIZATIONS",
                f"expected 1 authorization, PayPal returned {len(auths)}",
            )
        a = auths[0]

        order = self._lock(PaymentOrder, order_id)
        if order.authorization:  # a concurrent request got here first
            self.session.commit()
            return order.authorization
        self._check_amount(order, a["amount"], "authorization")
        payee = (a.get("payee") or resp["purchase_units"][0].get("payee") or {}).get("merchant_id")
        self._pin_merchant(order.merchant_account_id, payee)

        created = _parse_ts(a.get("create_time"))
        auth = PaymentAuthorization(
            payment_order_id=order.id,
            paypal_authorization_id=a["id"],
            amount_minor=order.amount_minor,
            currency=order.currency,
            status=_map_auth_status(a["status"]),
            paypal_status=a["status"],
            payee_merchant_id=payee,
            paypal_created_at=created,
            expires_at=_parse_ts(a.get("expiration_time")),
            honor_period_ends_at=(created + HONOR_PERIOD) if created else None,
            last_paypal_response=redact(a),
        )
        self.session.add(auth)
        order.status = OrderStatus.AUTHORIZED
        order.paypal_status = resp.get("status")
        self.session.flush()
        record_audit(
            self.session,
            actor=actor,
            action="payment.authorization.created",
            entity_type="payment_authorization",
            entity_id=auth.id,
            data={
                "paypal_authorization_id": auth.paypal_authorization_id,
                "paypal_status": auth.paypal_status,
                "amount": str(self._money(auth)),
                "expires_at": auth.expires_at,
            },
        )
        self.session.commit()
        return auth

    def refresh_authorization(self, auth_id: str) -> PaymentAuthorization:
        auth = self._get(PaymentAuthorization, auth_id)
        resp = self.gateway.get_authorization(auth.paypal_authorization_id)
        auth = self._lock(PaymentAuthorization, auth_id)
        self._apply_authorization(auth, resp)
        self.session.commit()
        return auth

    def _apply_authorization(self, auth: PaymentAuthorization, resp: Json) -> None:
        if Money.from_paypal(resp["amount"]) != self._money(auth):
            raise PaymentRuleViolation("AMOUNT_MISMATCH", "PayPal authorization amount changed")
        auth.paypal_status = resp["status"]
        auth.last_paypal_response = redact(resp)
        mapped = _map_auth_status(resp["status"])
        # Don't overwrite our in-flight marker with PayPal's still-CREATED view.
        if not (
            auth.status is AuthorizationStatus.CAPTURE_IN_PROGRESS
            and mapped is AuthorizationStatus.AUTHORIZED
        ):
            auth.status = mapped
        if (
            auth.expires_at
            and utcnow() >= auth.expires_at
            and auth.status is AuthorizationStatus.AUTHORIZED
        ):
            auth.status = AuthorizationStatus.EXPIRED

    # ----------------------------------------------------------------- capture

    def capture_authorization(
        self, auth_id: str, *, actor: str, note_to_payer: str | None = None
    ) -> PaymentCapture:
        # 0. Re-read PayPal's view before reserving (valid state, amount unchanged).
        auth = self._get(PaymentAuthorization, auth_id)
        remote = self.gateway.get_authorization(auth.paypal_authorization_id)

        # 1. Reserve.
        auth = self._lock(PaymentAuthorization, auth_id)
        self._apply_authorization(auth, remote)
        existing = auth.capture
        if existing and existing.status in (CaptureStatus.COMPLETED, CaptureStatus.PENDING):
            self.session.commit()
            raise PaymentRuleViolation(
                "ALREADY_CAPTURED", "this authorization was already captured"
            )
        if existing and existing.status in (CaptureStatus.REQUESTED, CaptureStatus.UNKNOWN):
            self.session.commit()
            raise PaymentRuleViolation(
                "CAPTURE_IN_PROGRESS", "a capture is in flight or uncertain — reconcile it first"
            )
        if auth.status is not AuthorizationStatus.AUTHORIZED:
            self.session.commit()
            raise PaymentRuleViolation(
                "AUTHORIZATION_NOT_CAPTURABLE", f"authorization is {auth.status.value}"
            )
        order = auth.order
        merchant = self.session.get(MerchantAccount, order.merchant_account_id)
        if merchant is None or (
            merchant.paypal_merchant_id and auth.payee_merchant_id != merchant.paypal_merchant_id
        ):
            self.session.commit()
            raise PaymentRuleViolation(
                "MERCHANT_MISMATCH", "authorization payee is not our merchant"
            )
        if (auth.amount_minor, auth.currency) != (order.amount_minor, order.currency):
            self.session.commit()
            raise PaymentRuleViolation("AMOUNT_MISMATCH", "authorization does not match the order")

        request_id = f"og-cap-{auth.id}-{uuid.uuid4().hex[:8]}"
        if existing:  # previous attempt FAILED/DECLINED at PayPal: no money moved, retry
            capture = existing
            capture.request_id = request_id
            capture.status = CaptureStatus.REQUESTED
            capture.error = None
            capture.requested_by = actor
        else:
            capture = PaymentCapture(
                payment_authorization_id=auth.id,
                request_id=request_id,
                amount_minor=auth.amount_minor,
                currency=auth.currency,
                final_capture=True,
                status=CaptureStatus.REQUESTED,
                requested_by=actor,
            )
            self.session.add(capture)
        auth.status = AuthorizationStatus.CAPTURE_IN_PROGRESS
        self.session.flush()
        record_audit(
            self.session,
            actor=actor,
            action="payment.capture.requested",
            entity_type="payment_capture",
            entity_id=capture.id,
            data={
                "authorization": auth.paypal_authorization_id,
                "amount": str(self._money(auth)),
                "request_id": request_id,
            },
        )
        self.session.commit()

        # 2. Call PayPal.  3. Settle.
        body: Json = {
            "amount": self._money(auth).to_paypal(),
            "final_capture": True,
            "invoice_id": order.reference,
        }
        if note_to_payer:
            body["note_to_payer"] = note_to_payer[:255]
        return self._send_capture(capture.id, auth.paypal_authorization_id, body)

    def _send_capture(self, capture_id: str, paypal_auth_id: str, body: Json) -> PaymentCapture:
        capture = self._get(PaymentCapture, capture_id)
        try:
            resp = self.gateway.capture_authorization(
                paypal_auth_id, body, request_id=capture.request_id
            )
        except PayPalUncertainError as exc:
            capture = self._lock(PaymentCapture, capture_id)
            capture.status = CaptureStatus.UNKNOWN
            capture.error = exc.to_record()
            record_audit(
                self.session,
                actor="system",
                action="payment.capture.unknown",
                entity_type="payment_capture",
                entity_id=capture.id,
                data=exc.to_record(),
            )
            self.session.commit()
            raise PaymentUncertain("payment_capture", capture.id) from None
        except PayPalAPIError as exc:
            capture = self._lock(PaymentCapture, capture_id)
            auth = capture.authorization
            capture.error = exc.to_record()
            capture.status = CaptureStatus.FAILED
            if "AUTHORIZATION_ALREADY_CAPTURED" in exc.issues:
                # PayPal says money already moved under another request: never retry blindly.
                capture.status = CaptureStatus.UNKNOWN
            elif auth.status is AuthorizationStatus.CAPTURE_IN_PROGRESS:
                auth.status = AuthorizationStatus.AUTHORIZED
            record_audit(
                self.session,
                actor="system",
                action=f"payment.capture.{capture.status.value}",
                entity_type="payment_capture",
                entity_id=capture.id,
                data=exc.to_record(),
            )
            self.session.commit()
            raise
        return self._settle_capture(capture_id, resp)

    def _settle_capture(self, capture_id: str, resp: Json) -> PaymentCapture:
        capture = self._lock(PaymentCapture, capture_id)
        auth = capture.authorization
        got = Money.from_paypal(resp["amount"])
        if got != self._money(capture):
            # Do not paper over this: record exactly what PayPal said and flag it.
            capture.status = CaptureStatus.UNKNOWN
            capture.error = {
                "amount_mismatch": {"expected": str(self._money(capture)), "paypal": str(got)}
            }
        else:
            new = _map_capture_status(resp["status"])
            # Never regress a completed capture (stale webhook / out-of-order read).
            if not (
                capture.status is CaptureStatus.COMPLETED and new is not CaptureStatus.COMPLETED
            ):
                capture.status = new
        capture.paypal_capture_id = resp["id"]
        capture.paypal_status = resp["status"]
        capture.last_paypal_response = redact(resp)
        if capture.status in (CaptureStatus.COMPLETED, CaptureStatus.PENDING):
            auth.status = AuthorizationStatus.CAPTURED
            auth.order.paypal_status = "COMPLETED"
        elif capture.status is CaptureStatus.DECLINED:
            auth.status = AuthorizationStatus.DENIED
        record_audit(
            self.session,
            actor="system",
            action=f"payment.capture.{capture.status.value}",
            entity_type="payment_capture",
            entity_id=capture.id,
            data={"paypal_capture_id": capture.paypal_capture_id, "paypal_status": resp["status"]},
        )
        self.session.commit()
        return capture

    def reconcile_capture(self, capture_id: str) -> PaymentCapture:
        """Resolve an UNKNOWN/REQUESTED capture.

        Replays the original PayPal-Request-Id: if PayPal processed it, PayPal returns the
        original capture instead of capturing again; if not, it is processed now — which is
        what the approver asked for.
        """
        capture = self._get(PaymentCapture, capture_id)
        if capture.paypal_capture_id:
            resp = self.gateway.get_capture(capture.paypal_capture_id)
            return self._settle_capture(capture_id, resp)
        if capture.status not in (CaptureStatus.REQUESTED, CaptureStatus.UNKNOWN):
            return capture
        auth = capture.authorization
        body = {
            "amount": self._money(capture).to_paypal(),
            "final_capture": True,
            "invoice_id": auth.order.reference,
        }
        return self._send_capture(capture_id, auth.paypal_authorization_id, body)

    # ------------------------------------------------------------------ refund

    def refundable_balance(self, capture: PaymentCapture) -> Money:
        held = sum(r.amount_minor for r in capture.refunds if r.status in REFUND_HOLDS_BALANCE)
        return Money(capture.amount_minor - held, capture.currency)

    def refund_capture(
        self,
        capture_id: str,
        *,
        amount: Money,
        idempotency_key: str,
        actor: str,
        note: str | None = None,
    ) -> PaymentRefund:
        if not _IDEMPOTENCY_KEY.match(idempotency_key):
            raise PaymentRuleViolation(
                "INVALID_IDEMPOTENCY_KEY", "8-64 chars of [A-Za-z0-9_-]", http_status=422
            )
        request_id = f"og-ref-{idempotency_key}"

        capture = self._lock(PaymentCapture, capture_id)
        prior = self.session.scalar(
            select(PaymentRefund).where(PaymentRefund.request_id == request_id)
        )
        if prior:
            self.session.commit()
            if (prior.payment_capture_id, prior.amount_minor, prior.currency) != (
                capture.id,
                amount.minor,
                amount.currency,
            ):
                raise PaymentRuleViolation(
                    "IDEMPOTENCY_KEY_REUSED", "key was used for a different refund", http_status=422
                )
            return prior  # same request replayed: return the original refund
        if capture.status is not CaptureStatus.COMPLETED:
            self.session.commit()
            raise PaymentRuleViolation(
                "CAPTURE_NOT_REFUNDABLE", f"capture is {capture.status.value}"
            )
        if amount.currency != capture.currency:
            self.session.commit()
            raise PaymentRuleViolation(
                "CURRENCY_MISMATCH", "refund currency differs", http_status=422
            )
        if amount.minor <= 0:
            self.session.commit()
            raise PaymentRuleViolation("INVALID_AMOUNT", "refund must be positive", http_status=422)
        remaining = self.refundable_balance(capture)
        if amount.minor > remaining.minor:
            self.session.commit()
            raise PaymentRuleViolation(
                "REFUND_EXCEEDS_BALANCE", f"refundable balance is {remaining}", http_status=422
            )
        refund = PaymentRefund(
            payment_capture_id=capture.id,
            request_id=request_id,
            amount_minor=amount.minor,
            currency=amount.currency,
            status=RefundStatus.REQUESTED,
            note=(note or None) and note[:255],
            requested_by=actor,
        )
        self.session.add(refund)
        self.session.flush()
        record_audit(
            self.session,
            actor=actor,
            action="payment.refund.requested",
            entity_type="payment_refund",
            entity_id=refund.id,
            data={
                "capture": capture.paypal_capture_id,
                "amount": str(amount),
                "remaining_before": str(remaining),
            },
        )
        self.session.commit()
        return self._send_refund(refund.id)

    def _send_refund(self, refund_id: str) -> PaymentRefund:
        refund = self._get(PaymentRefund, refund_id)
        capture = refund.capture
        body: Json = {
            "amount": self._money(refund).to_paypal(),
            "invoice_id": f"{capture.authorization.order.reference}-r-{refund.id[:8]}",
        }
        if refund.note:
            body["note_to_payer"] = refund.note
        try:
            resp = self.gateway.refund_capture(
                _require(capture.paypal_capture_id, "paypal_capture_id"),
                body,
                request_id=refund.request_id,
            )
        except PayPalUncertainError as exc:
            refund = self._lock(PaymentRefund, refund_id)
            refund.status = RefundStatus.UNKNOWN
            refund.error = exc.to_record()
            record_audit(
                self.session,
                actor="system",
                action="payment.refund.unknown",
                entity_type="payment_refund",
                entity_id=refund.id,
                data=exc.to_record(),
            )
            self.session.commit()
            raise PaymentUncertain("payment_refund", refund.id) from None
        except PayPalAPIError as exc:
            refund = self._lock(PaymentRefund, refund_id)
            refund.status = RefundStatus.FAILED
            refund.error = exc.to_record()
            record_audit(
                self.session,
                actor="system",
                action="payment.refund.failed",
                entity_type="payment_refund",
                entity_id=refund.id,
                data=exc.to_record(),
            )
            self.session.commit()
            raise
        return self._settle_refund(refund_id, resp)

    def _settle_refund(self, refund_id: str, resp: Json) -> PaymentRefund:
        refund = self._lock(PaymentRefund, refund_id)
        got = Money.from_paypal(resp["amount"])
        refund.paypal_refund_id = resp["id"]
        refund.paypal_status = resp["status"]
        refund.last_paypal_response = redact(resp)
        if got != self._money(refund):
            refund.status = RefundStatus.UNKNOWN
            refund.error = {
                "amount_mismatch": {"expected": str(self._money(refund)), "paypal": str(got)}
            }
        else:
            new = _map_refund_status(resp["status"])
            if not (refund.status is RefundStatus.COMPLETED and new is not RefundStatus.COMPLETED):
                refund.status = new
        record_audit(
            self.session,
            actor="system",
            action=f"payment.refund.{refund.status.value}",
            entity_type="payment_refund",
            entity_id=refund.id,
            data={"paypal_refund_id": refund.paypal_refund_id, "paypal_status": resp["status"]},
        )
        self.session.commit()
        return refund

    def reconcile_refund(self, refund_id: str) -> PaymentRefund:
        refund = self._get(PaymentRefund, refund_id)
        if refund.paypal_refund_id:
            return self._settle_refund(refund_id, self.gateway.get_refund(refund.paypal_refund_id))
        if refund.status in (RefundStatus.REQUESTED, RefundStatus.UNKNOWN):
            return self._send_refund(refund_id)
        return refund

    # ----------------------------------------------------------------- helpers

    def _lock[T](self, model: type[T], pk: str) -> T:
        # Expire the identity map so relationships (order.authorization, auth.capture,
        # capture.refunds) are re-read inside this locked transaction, not served stale.
        self.session.expire_all()
        row = self.session.scalar(
            select(model).where(model.id == pk).with_for_update()  # type: ignore[attr-defined]
        )
        if row is None:
            self.session.rollback()
            raise PaymentRuleViolation(
                "NOT_FOUND", f"{model.__name__} {pk} not found", http_status=404
            )
        return row

    def _get[T](self, model: type[T], pk: str) -> T:
        row = self.session.get(model, pk, populate_existing=True)
        if row is None:
            raise PaymentRuleViolation(
                "NOT_FOUND", f"{model.__name__} {pk} not found", http_status=404
            )
        return row

    @staticmethod
    def _money(row: Any) -> Money:
        return Money(row.amount_minor, row.currency)

    def _check_amount(self, order: PaymentOrder, amount: Json, what: str) -> None:
        try:
            got = Money.from_paypal(amount)
        except (MoneyError, KeyError) as exc:
            raise PaymentRuleViolation("AMOUNT_MISMATCH", f"unreadable {what} amount") from exc
        if got != self._money(order):
            raise PaymentRuleViolation(
                "AMOUNT_MISMATCH", f"{what} amount {got} != order amount {self._money(order)}"
            )

    def _check_order_identity(self, order: PaymentOrder, resp: Json) -> None:
        if resp.get("id") != order.paypal_order_id:
            raise PaymentRuleViolation("ORDER_MISMATCH", "PayPal returned a different order")
        if resp.get("intent") not in (None, "AUTHORIZE"):
            raise PaymentRuleViolation("INTENT_MISMATCH", f"order intent is {resp.get('intent')}")
        pu = resp["purchase_units"][0]
        if pu.get("reference_id") not in (None, order.reference):
            raise PaymentRuleViolation("ORDER_MISMATCH", "purchase unit reference differs")
        self._check_amount(order, pu["amount"], "order")

    def _pin_merchant(self, merchant_id: str, payee: str | None) -> None:
        merchant = self._lock(MerchantAccount, merchant_id)
        if not payee:
            raise PaymentRuleViolation("MERCHANT_MISMATCH", "PayPal did not report a payee")
        if merchant.paypal_merchant_id is None:
            merchant.paypal_merchant_id = payee
            record_audit(
                self.session,
                actor="system",
                action="merchant.paypal_id.pinned",
                entity_type="merchant_account",
                entity_id=merchant.id,
                data={"payee": payee},
            )
        elif merchant.paypal_merchant_id != payee:
            raise PaymentRuleViolation(
                "MERCHANT_MISMATCH", "authorization payee differs from the configured merchant"
            )


def _require(value: str | None, name: str) -> str:
    if not value:
        raise PaymentRuleViolation("MISSING_REFERENCE", f"{name} is not set")
    return value


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _map_auth_status(s: str) -> AuthorizationStatus:
    return {
        "CREATED": AuthorizationStatus.AUTHORIZED,
        "PENDING": AuthorizationStatus.PENDING,
        "CAPTURED": AuthorizationStatus.CAPTURED,
        "PARTIALLY_CAPTURED": AuthorizationStatus.CAPTURED,
        "DENIED": AuthorizationStatus.DENIED,
        "VOIDED": AuthorizationStatus.VOIDED,
        "EXPIRED": AuthorizationStatus.EXPIRED,
    }.get(s, AuthorizationStatus.PENDING)


def _map_capture_status(s: str) -> CaptureStatus:
    return {
        "COMPLETED": CaptureStatus.COMPLETED,
        # Money was captured; refunds are tracked as their own rows.
        "PARTIALLY_REFUNDED": CaptureStatus.COMPLETED,
        "REFUNDED": CaptureStatus.COMPLETED,
        "PENDING": CaptureStatus.PENDING,
        "DECLINED": CaptureStatus.DECLINED,
        "FAILED": CaptureStatus.FAILED,
    }.get(s, CaptureStatus.UNKNOWN)


def _map_refund_status(s: str) -> RefundStatus:
    return {
        "COMPLETED": RefundStatus.COMPLETED,
        "PENDING": RefundStatus.PENDING,
        "FAILED": RefundStatus.FAILED,
        "CANCELLED": RefundStatus.CANCELLED,
    }.get(s, RefundStatus.UNKNOWN)
