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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from outcomeguard.config import Settings
from outcomeguard.logs import redact
from outcomeguard.models import (
    AgreementVersion,
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
    VersionStatus,
)
from outcomeguard.models.audit import record_audit
from outcomeguard.models.base import utcnow
from outcomeguard.models.payments import REFUND_HOLDS_BALANCE
from outcomeguard.models.resolution import AuthorizationVoid, VoidStatus
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


@dataclass
class CaptureRefs:
    """What a governance gate returns: the evidence chain for this capture."""

    policy_decision_id: str
    approval_id: str
    verification_run_id: str
    on_reserved: Callable[[PaymentCapture], None] | None = None


CaptureGate = Callable[["PaymentAuthorization"], CaptureRefs]


@dataclass
class RefundRefs:
    """What the P08 resolution gate returns: the governance chain for this refund."""

    resolution_case_id: str
    proposal_id: str
    policy_decision_id: str
    on_reserved: Callable[[PaymentRefund], None] | None = None


RefundGate = Callable[["PaymentCapture", Money], RefundRefs]


@dataclass
class VoidRefs:
    policy_decision_id: str
    case_id: str | None = None
    proposal_id: str | None = None


VoidGate = Callable[["PaymentAuthorization"], VoidRefs]

# A lost refund is NEVER re-sent automatically. PayPal does not document how long the
# v2 refund endpoint honours a PayPal-Request-Id (TD-17, checked 2026-10-16), so a replay
# could become a second refund. Reconciliation only reads; if PayPal shows no matching
# refund, the operation stops in RECONCILIATION_REQUIRED (amount held) for an operator.


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
    last_reconcile_method: str | None = None

    @property
    def environment(self) -> PaymentEnvironment:
        return (
            PaymentEnvironment.SIMULATED if self.gateway.simulated else PaymentEnvironment.SANDBOX
        )

    # ------------------------------------------------------------------ orders

    def create_order(
        self,
        *,
        merchant: MerchantAccount,
        amount: Money,
        description: str,
        actor: str,
        agreement_version: AgreementVersion | None = None,
    ) -> PaymentOrder:
        """Create a PayPal order. Product flows must pass the LOCKED agreement version the
        payment is for; only the P02 sandbox-proof driver omits it."""
        if agreement_version is not None:
            self._check_agreement_binding(agreement_version, merchant, amount)
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
            agreement_version_id=agreement_version.id if agreement_version else None,
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

    def _check_agreement_binding(
        self, version: AgreementVersion, merchant: MerchantAccount, amount: Money
    ) -> None:
        if version.status is not VersionStatus.LOCKED:
            raise PaymentRuleViolation(
                "AGREEMENT_NOT_LOCKED",
                f"agreement version is {version.status.value}; only a locked version can be paid",
            )
        if version.agreement.merchant_account_id != merchant.id:
            raise PaymentRuleViolation("MERCHANT_MISMATCH", "merchant differs from the agreement")
        if amount != Money(version.amount_minor, version.currency):
            raise PaymentRuleViolation(
                "AMOUNT_MISMATCH", "payment amount differs from the locked agreement amount"
            )

    def create_order_for_agreement(self, version: AgreementVersion, *, actor: str) -> PaymentOrder:
        """Amount, currency and merchant come from the locked version — never from a client."""
        merchant = self.session.get(MerchantAccount, version.agreement.merchant_account_id)
        if merchant is None:
            raise PaymentRuleViolation("MERCHANT_MISMATCH", "agreement merchant not found")
        return self.create_order(
            merchant=merchant,
            amount=Money(version.amount_minor, version.currency),
            description=f"{version.title} (v{version.version_number})",
            actor=actor,
            agreement_version=version,
        )

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
                    _require(order.paypal_order_id, "paypal_order_id"),
                    request_id=f"og-auth-{order.id}",
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
        # Don't overwrite our in-flight markers with PayPal's still-CREATED view.
        if not (
            auth.status
            in (AuthorizationStatus.CAPTURE_IN_PROGRESS, AuthorizationStatus.VOID_IN_PROGRESS)
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
        self,
        auth_id: str,
        *,
        actor: str,
        note_to_payer: str | None = None,
        gate: CaptureGate | None = None,
    ) -> PaymentCapture:
        """Capture an authorization. For agreement-bound orders a governance `gate` is
        mandatory: it runs inside the reservation transaction, while the authorization row
        is locked, and must return the policy/approval/verification references — otherwise
        nothing is reserved (POLICY_REQUIRED). The PayPal call happens after commit."""
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
        refs: CaptureRefs | None = None
        if order.agreement_version_id is not None:
            if gate is None:
                self.session.rollback()
                raise PaymentRuleViolation(
                    "POLICY_REQUIRED",
                    "agreement-bound payments can only be captured through the governance "
                    "policy (fresh evaluation + human approval)",
                    http_status=403,
                )
            try:
                refs = gate(auth)  # same transaction, authorization row still locked
            except Exception:
                self.session.rollback()
                raise

        request_id = f"og-cap-{auth.id}-{uuid.uuid4().hex[:8]}"
        if existing:  # previous attempt FAILED/DECLINED at PayPal: no money moved, retry
            capture = existing
            capture.request_id = request_id
            capture.status = CaptureStatus.REQUESTED
            capture.error = None
            capture.requested_by = actor
            if refs:
                capture.policy_decision_id = refs.policy_decision_id
                capture.approval_id = refs.approval_id
                capture.verification_run_id = refs.verification_run_id
        else:
            capture = PaymentCapture(
                payment_authorization_id=auth.id,
                request_id=request_id,
                amount_minor=auth.amount_minor,
                currency=auth.currency,
                final_capture=True,
                status=CaptureStatus.REQUESTED,
                requested_by=actor,
                policy_decision_id=refs.policy_decision_id if refs else None,
                approval_id=refs.approval_id if refs else None,
                verification_run_id=refs.verification_run_id if refs else None,
            )
            self.session.add(capture)
        auth.status = AuthorizationStatus.CAPTURE_IN_PROGRESS
        self.session.flush()
        if refs and refs.on_reserved:
            refs.on_reserved(capture)
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
        capture = self._settle_capture(capture_id, resp)
        self._refresh_mirrors(authorization_id=capture.payment_authorization_id)
        return capture

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
        _apply_capture_breakdown(capture, resp)
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
        # Read first: if PayPal already has a capture for this authorization (our invoice
        # id), settle from it — no further POST is sent.
        found = self._find_remote_capture(auth)
        if found is not None:
            self.last_reconcile_method = "read_existing_capture"
            return self._settle_capture(capture_id, self.gateway.get_capture(found))
        self.last_reconcile_method = "replay_same_request_id"
        body = {
            "amount": self._money(capture).to_paypal(),
            "final_capture": True,
            "invoice_id": auth.order.reference,
        }
        return self._send_capture(capture_id, auth.paypal_authorization_id, body)

    def _find_remote_capture(self, auth: PaymentAuthorization) -> str | None:
        """The PayPal capture id for this authorization, read from the order, or None.
        More than one candidate → None (never guess; fall back to the idempotent replay)."""
        order = auth.order
        if not order.paypal_order_id:
            return None
        resp = self.gateway.get_order(order.paypal_order_id)
        caps = resp["purchase_units"][0].get("payments", {}).get("captures", [])
        matches = [
            c["id"]
            for c in caps
            if c.get("invoice_id") == order.reference
            or any(
                auth.paypal_authorization_id in (lk.get("href") or "") for lk in c.get("links", [])
            )
        ]
        return matches[0] if len(matches) == 1 else None

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
        gate: RefundGate | None = None,
    ) -> PaymentRefund:
        """Refund (part of) a completed capture. For agreement-bound captures the P08
        resolution `gate` is mandatory: it runs under the capture row lock, re-evaluates
        the refund policy and returns the case / proposal / decision references —
        otherwise nothing is reserved (POLICY_REQUIRED)."""
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
        refs: RefundRefs | None = None
        if capture.authorization.order.agreement_version_id is not None:
            if gate is None:
                self.session.rollback()
                raise PaymentRuleViolation(
                    "POLICY_REQUIRED",
                    "agreement-bound captures can only be refunded through an agreed "
                    "resolution (both parties' approval + fresh refund policy)",
                    http_status=403,
                )
            try:
                refs = gate(capture, amount)  # same transaction, capture row still locked
            except Exception:
                self.session.rollback()
                raise
        refund = PaymentRefund(
            payment_capture_id=capture.id,
            request_id=request_id,
            amount_minor=amount.minor,
            currency=amount.currency,
            status=RefundStatus.REQUESTED,
            note=(note or None) and note[:255],
            requested_by=actor,
            resolution_case_id=refs.resolution_case_id if refs else None,
            proposal_id=refs.proposal_id if refs else None,
            policy_decision_id=refs.policy_decision_id if refs else None,
        )
        self.session.add(refund)
        self.session.flush()
        if refs and refs.on_reserved:
            refs.on_reserved(refund)
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

    @staticmethod
    def refund_invoice_id(refund: PaymentRefund) -> str:
        return f"{refund.capture.authorization.order.reference}-r-{refund.id[:8]}"

    def _send_refund(self, refund_id: str) -> PaymentRefund:
        refund = self._get(PaymentRefund, refund_id)
        body: Json = {
            "amount": self._money(refund).to_paypal(),
            "invoice_id": self.refund_invoice_id(refund),
        }
        if refund.note:
            body["note_to_payer"] = refund.note
        try:
            resp = self.gateway.refund_capture(
                _require(refund.capture.paypal_capture_id, "paypal_capture_id"),
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
        refund = self._settle_refund(refund_id, resp)
        self._refresh_mirrors(capture_id=refund.payment_capture_id)
        return refund

    def _settle_refund(self, refund_id: str, resp: Json) -> PaymentRefund:
        refund = self._lock(PaymentRefund, refund_id)
        got = Money.from_paypal(resp["amount"])
        refund.paypal_refund_id = resp["id"]
        refund.paypal_status = resp["status"]
        refund.last_paypal_response = redact(resp)
        _apply_refund_breakdown(refund, resp)
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
        """Resolve a refund whose outcome is not final. Read only — never a POST:

        1. a persisted PayPal refund id → GET the refund;
        2. otherwise find our refund on the PayPal order (`invoice_id`, exactly one
           match) → settle from it;
        3. otherwise RECONCILIATION_REQUIRED: the amount stays held and an operator
           decides. The state is not terminal — a later reconcile reads PayPal again
           (covers eventually consistent reads) and settles if the refund appears."""
        refund = self._get(PaymentRefund, refund_id)
        self.last_reconcile_method = None
        if refund.paypal_refund_id:
            self.last_reconcile_method = "read_refund"
            return self._settle_refund(refund_id, self.gateway.get_refund(refund.paypal_refund_id))
        if refund.status not in (
            RefundStatus.REQUESTED,
            RefundStatus.UNKNOWN,
            RefundStatus.RECONCILIATION_REQUIRED,
        ):
            return refund
        found = self._find_remote_refund(refund)
        if found == "ambiguous":
            return self._needs_operator(refund_id, "several PayPal refunds match this operation")
        if found is not None:
            self.last_reconcile_method = "read_existing_refund"
            return self._settle_refund(refund_id, self.gateway.get_refund(found))
        return self._needs_operator(
            refund_id,
            "no matching refund on the PayPal order; automatic re-sending is disabled "
            "because PayPal does not document how long it honours the request id",
        )

    def _find_remote_refund(self, refund: PaymentRefund) -> str | None:
        order = refund.capture.authorization.order
        if not order.paypal_order_id:
            return None
        resp = self.gateway.get_order(order.paypal_order_id)
        refunds = resp["purchase_units"][0].get("payments", {}).get("refunds", [])
        invoice = self.refund_invoice_id(refund)
        matches = [r["id"] for r in refunds if r.get("invoice_id") == invoice]
        if len(matches) > 1:
            return "ambiguous"
        return matches[0] if matches else None

    def _needs_operator(self, refund_id: str, why: str) -> PaymentRefund:
        refund = self._lock(PaymentRefund, refund_id)
        refund.status = RefundStatus.RECONCILIATION_REQUIRED
        refund.error = {**(refund.error or {}), "reconciliation": why}
        self.last_reconcile_method = "operator_required"
        record_audit(
            self.session,
            actor="system",
            action="payment.refund.reconciliation_required",
            entity_type="payment_refund",
            entity_id=refund.id,
            data={"reason": why},
        )
        self.session.commit()
        return refund

    # -------------------------------------------------------------------- void

    def void_authorization(
        self, auth_id: str, *, actor: str, reason: str, gate: VoidGate
    ) -> AuthorizationVoid:
        """Void an uncaptured authorization (not a refund: no money had moved).

        Reserve under the authorization row lock — the same lock capture uses — so a void
        and a capture can never both proceed: the void marks the authorization
        VOID_IN_PROGRESS (capture requires AUTHORIZED) and refuses while a capture row is
        requested/unknown/pending/completed. The governance `gate` re-evaluates the void
        policy inside the lock. One operation per authorization; repeats return it."""
        auth = self._get(PaymentAuthorization, auth_id)
        remote = self.gateway.get_authorization(auth.paypal_authorization_id)
        auth = self._lock(PaymentAuthorization, auth_id)
        self._apply_authorization(auth, remote)
        op = self.session.scalar(
            select(AuthorizationVoid).where(AuthorizationVoid.payment_authorization_id == auth.id)
        )
        if op is not None and op.status is VoidStatus.COMPLETED:
            self.session.commit()
            return op  # repeated request: the original result
        try:
            refs = gate(auth)  # raises when the policy blocks
        except Exception:
            self.session.rollback()
            raise
        request_id = f"og-void-{auth.id}"
        if op is None:
            op = AuthorizationVoid(
                payment_authorization_id=auth.id,
                request_id=request_id,
                status=VoidStatus.REQUESTED,
                requested_by=actor,
                reason=reason,
                policy_decision_id=refs.policy_decision_id,
                case_id=refs.case_id,
                proposal_id=refs.proposal_id,
            )
            self.session.add(op)
        else:  # previous attempt FAILED at PayPal: nothing changed, try again
            op.status = VoidStatus.REQUESTED
            op.error = None
            op.requested_by = actor
            op.reason = reason
            op.policy_decision_id = refs.policy_decision_id
            op.case_id = refs.case_id
            op.proposal_id = refs.proposal_id
        auth.status = AuthorizationStatus.VOID_IN_PROGRESS
        self.session.flush()
        record_audit(
            self.session,
            actor=actor,
            action="payment.void.requested",
            entity_type="authorization_void",
            entity_id=op.id,
            data={"authorization": auth.paypal_authorization_id, "request_id": request_id},
        )
        self.session.commit()
        return self._send_void(op.id)

    def _send_void(self, void_id: str) -> AuthorizationVoid:
        op = self._get(AuthorizationVoid, void_id)
        auth = self._get(PaymentAuthorization, op.payment_authorization_id)
        try:
            self.gateway.void_authorization(auth.paypal_authorization_id, request_id=op.request_id)
        except PayPalUncertainError as exc:
            op = self._lock(AuthorizationVoid, void_id)
            op.status = VoidStatus.UNKNOWN
            op.error = exc.to_record()
            record_audit(
                self.session,
                actor="system",
                action="payment.void.unknown",
                entity_type="authorization_void",
                entity_id=op.id,
                data=exc.to_record(),
            )
            self.session.commit()
            raise PaymentUncertain("authorization_void", op.id) from None
        except PayPalAPIError as exc:
            # PayPal refused; read what the authorization actually is now.
            return self._settle_void(void_id, failure=exc)
        return self._settle_void(void_id)

    def _settle_void(
        self, void_id: str, failure: PayPalAPIError | None = None
    ) -> AuthorizationVoid:
        op = self._get(AuthorizationVoid, void_id)
        auth = self._get(PaymentAuthorization, op.payment_authorization_id)
        remote = self.gateway.get_authorization(auth.paypal_authorization_id)
        op = self._lock(AuthorizationVoid, void_id)
        auth = self._lock(PaymentAuthorization, op.payment_authorization_id)
        op.paypal_status = remote.get("status")
        if remote.get("status") == "VOIDED":
            op.status = VoidStatus.COMPLETED
            op.error = None
            auth.status = AuthorizationStatus.AUTHORIZED  # let the mapper set VOIDED
        else:
            op.status = VoidStatus.FAILED
            op.error = failure.to_record() if failure else {"paypal_status": remote.get("status")}
            if auth.status is AuthorizationStatus.VOID_IN_PROGRESS:
                auth.status = AuthorizationStatus.AUTHORIZED
        self._apply_authorization(auth, remote)
        record_audit(
            self.session,
            actor="system",
            action=f"payment.void.{op.status.value}",
            entity_type="authorization_void",
            entity_id=op.id,
            data={"paypal_status": op.paypal_status},
        )
        self.session.commit()
        if failure is not None and op.status is VoidStatus.FAILED:
            raise failure
        return op

    def reconcile_void(self, void_id: str) -> AuthorizationVoid:
        """Read first: VOIDED → completed; CAPTURED/other final → failed (the capture path
        reconciles money); still CREATED → replay the same PayPal-Request-Id (a void is
        idempotent at PayPal)."""
        op = self._get(AuthorizationVoid, void_id)
        if op.status not in (VoidStatus.REQUESTED, VoidStatus.UNKNOWN):
            return op
        auth = self._get(PaymentAuthorization, op.payment_authorization_id)
        status = self.gateway.get_authorization(auth.paypal_authorization_id).get("status")
        if status == "CREATED":
            self.last_reconcile_method = "replay_same_request_id"
            return self._send_void(void_id)
        self.last_reconcile_method = "read_authorization"
        return self._settle_void(void_id)

    # ----------------------------------------------------------------- helpers

    def _refresh_mirrors(
        self, *, authorization_id: str | None = None, capture_id: str | None = None
    ) -> None:
        """After money moved, re-read the *parent* object from PayPal so `paypal_status`
        shows what PayPal reports (e.g. authorization CAPTURED, capture
        PARTIALLY_REFUNDED) instead of a stale or inferred value. Best effort: a failed
        read leaves the previous observation and is retried by later reconciliation."""
        try:
            if authorization_id:
                self.refresh_authorization(authorization_id)
            if capture_id:
                cap = self._get(PaymentCapture, capture_id)
                resp = self.gateway.get_capture(_require(cap.paypal_capture_id, "capture id"))
                cap = self._lock(PaymentCapture, capture_id)
                cap.paypal_status = resp["status"]
                cap.last_paypal_response = redact(resp)
                _apply_capture_breakdown(cap, resp)
                self.session.commit()
        except (PayPalAPIError, PayPalUncertainError, PaymentRuleViolation) as exc:
            self.session.rollback()
            log.warning("mirror refresh failed", extra={"data": {"error": str(exc)}})

    def _lock[T](self, model: type[T], pk: str) -> T:
        # Expire the identity map so relationships (order.authorization, auth.capture,
        # capture.refunds) are re-read inside this locked transaction, not served stale.
        self.session.expire_all()
        stmt: Any = select(model).where(model.id == pk).with_for_update()  # type: ignore[attr-defined]
        row = self.session.scalar(stmt)
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


def _reported(breakdown: Json | None, key: str, currency: str) -> int | None:
    """Minor units of a PayPal-reported breakdown amount, or None if absent/foreign."""
    amount = (breakdown or {}).get(key)
    if not amount:
        return None
    try:
        m = Money.from_paypal(amount)
    except (MoneyError, KeyError):
        return None
    return m.minor if m.currency == currency else None


def _apply_capture_breakdown(capture: PaymentCapture, resp: Json) -> None:
    b = resp.get("seller_receivable_breakdown")
    if b is None:
        return
    capture.paypal_fee_minor = _reported(b, "paypal_fee", capture.currency)
    capture.net_minor = _reported(b, "net_amount", capture.currency)


def _apply_refund_breakdown(refund: PaymentRefund, resp: Json) -> None:
    b = resp.get("seller_payable_breakdown")
    if b is None:
        return
    refund.paypal_fee_returned_minor = _reported(b, "paypal_fee", refund.currency)
    refund.merchant_debit_minor = _reported(b, "net_amount", refund.currency)


@dataclass(frozen=True)
class SettlementSummary:
    """Financial view of one capture, built only from PayPal-reported numbers.

    `net_after_refunds` is *derived* (capture net minus merchant debits of completed
    refunds); it is not a balance read from PayPal and is None whenever any input was
    not reported by PayPal.
    """

    currency: str
    gross_captured: int
    paypal_fee: int | None
    net_at_capture: int | None
    refunded_gross: int
    refund_fees_returned: int | None
    refund_merchant_debits: int | None
    net_after_refunds: int | None
    refunds_in_flight: int


def settlement_summary(capture: PaymentCapture) -> SettlementSummary:
    done = [r for r in capture.refunds if r.status is RefundStatus.COMPLETED]
    in_flight = [
        r
        for r in capture.refunds
        if r.status in (RefundStatus.REQUESTED, RefundStatus.PENDING, RefundStatus.UNKNOWN)
    ]

    def total(values: list[int | None]) -> int | None:
        return None if any(v is None for v in values) else sum(v for v in values if v is not None)

    fees_returned = total([r.paypal_fee_returned_minor for r in done])
    debits = total([r.merchant_debit_minor for r in done])
    net_after = (
        capture.net_minor - debits
        if capture.net_minor is not None and debits is not None and not in_flight
        else None
    )
    return SettlementSummary(
        currency=capture.currency,
        gross_captured=capture.amount_minor if capture.status is CaptureStatus.COMPLETED else 0,
        paypal_fee=capture.paypal_fee_minor,
        net_at_capture=capture.net_minor,
        refunded_gross=sum(r.amount_minor for r in done),
        refund_fees_returned=fees_returned,
        refund_merchant_debits=debits,
        net_after_refunds=net_after,
        refunds_in_flight=sum(r.amount_minor for r in in_flight),
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
