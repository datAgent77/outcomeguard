"""Payment records mirroring PayPal Orders v2 / Payments v2 objects.

Every row stores both our internal state (`status`) and the last status PayPal reported
(`paypal_status`), plus the raw (redacted) response, so the ledger can always show what
PayPal actually said. Amounts are integer minor units.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from outcomeguard.models.base import Base, IdMixin, TimestampMixin, enum_col
from outcomeguard.models.identity import PaymentEnvironment


class OrderStatus(StrEnum):
    CREATED = "created"  # order exists at PayPal, buyer has not approved
    APPROVED = "approved"  # PayPal reports APPROVED (read server-side, not from redirect)
    AUTHORIZED = "authorized"  # authorization created
    CANCELLED = "cancelled"  # buyer cancelled at PayPal
    FAILED = "failed"


class AuthorizationStatus(StrEnum):
    AUTHORIZED = "authorized"  # PayPal CREATED — funds held, capturable
    PENDING = "pending"  # PayPal PENDING — not capturable yet
    CAPTURE_IN_PROGRESS = "capture_in_progress"
    CAPTURED = "captured"
    DENIED = "denied"
    VOIDED = "voided"
    EXPIRED = "expired"


class CaptureStatus(StrEnum):
    REQUESTED = "requested"  # row reserved, request not yet answered
    UNKNOWN = "unknown"  # request sent, outcome uncertain (timeout/5xx) — must reconcile
    PENDING = "pending"  # PayPal PENDING
    COMPLETED = "completed"
    DECLINED = "declined"
    FAILED = "failed"  # PayPal rejected the request (4xx) — no money moved


class RefundStatus(StrEnum):
    REQUESTED = "requested"
    UNKNOWN = "unknown"
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Refund statuses that count against the refundable balance.
REFUND_HOLDS_BALANCE = (
    RefundStatus.REQUESTED,
    RefundStatus.UNKNOWN,
    RefundStatus.PENDING,
    RefundStatus.COMPLETED,
)


class PaymentOrder(IdMixin, TimestampMixin, Base):
    __tablename__ = "payment_orders"
    __table_args__ = (CheckConstraint("amount_minor > 0", name="amount_positive"),)

    merchant_account_id: Mapped[str] = mapped_column(ForeignKey("merchant_accounts.id"))
    # The locked agreement version this payment is for. Nullable only for the P02
    # sandbox-proof driver; product orders always set it and it must be LOCKED.
    agreement_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("agreement_versions.id"), index=True
    )
    environment: Mapped[PaymentEnvironment] = mapped_column(
        enum_col(PaymentEnvironment, "payment_environment")
    )
    # Our reference sent to PayPal as purchase_units[0].reference_id / custom_id.
    reference: Mapped[str] = mapped_column(String(64), unique=True)
    create_request_id: Mapped[str] = mapped_column(String(64), unique=True)
    paypal_order_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    description: Mapped[str] = mapped_column(String(127))
    status: Mapped[OrderStatus] = mapped_column(enum_col(OrderStatus, "order_status"))
    paypal_status: Mapped[str | None] = mapped_column(String(40))
    approval_url: Mapped[str | None] = mapped_column(Text)
    payer_id: Mapped[str | None] = mapped_column(String(64))
    last_paypal_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    authorization: Mapped[PaymentAuthorization | None] = relationship(
        back_populates="order", uselist=False
    )


class PaymentAuthorization(IdMixin, TimestampMixin, Base):
    __tablename__ = "payment_authorizations"
    __table_args__ = (CheckConstraint("amount_minor > 0", name="amount_positive"),)

    # One authorization per order in this build (unique).
    payment_order_id: Mapped[str] = mapped_column(ForeignKey("payment_orders.id"), unique=True)
    paypal_authorization_id: Mapped[str] = mapped_column(String(64), unique=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[AuthorizationStatus] = mapped_column(
        enum_col(AuthorizationStatus, "authorization_status")
    )
    paypal_status: Mapped[str] = mapped_column(String(40))
    payee_merchant_id: Mapped[str | None] = mapped_column(String(64))
    paypal_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # PayPal: authorization valid for 29 days; funds honored for 3 days.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    honor_period_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_paypal_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    order: Mapped[PaymentOrder] = relationship(back_populates="authorization")
    capture: Mapped[PaymentCapture | None] = relationship(
        back_populates="authorization", uselist=False
    )


class PaymentCapture(IdMixin, TimestampMixin, Base):
    """At most one capture per authorization (full, final capture).

    The unique constraint on `payment_authorization_id` is the database-level guard
    against double capture; `request_id` is sent as PayPal-Request-Id so a retried
    request is idempotent at PayPal too.
    """

    __tablename__ = "payment_captures"
    __table_args__ = (CheckConstraint("amount_minor > 0", name="amount_positive"),)

    payment_authorization_id: Mapped[str] = mapped_column(
        ForeignKey("payment_authorizations.id"), unique=True
    )
    request_id: Mapped[str] = mapped_column(String(64), unique=True)
    paypal_capture_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    final_capture: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[CaptureStatus] = mapped_column(enum_col(CaptureStatus, "capture_status"))
    paypal_status: Mapped[str | None] = mapped_column(String(40))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    last_paypal_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    requested_by: Mapped[str] = mapped_column(String(200))
    # Evidence chain for the decision to capture. `approval_id` points at the human
    # approval (approvals.subject_type = 'capture'). verification_run_id and
    # policy_decision_id gain foreign keys when those tables land (P04 / P06); the policy
    # engine will refuse to capture unless all of them are set.
    # PayPal-reported seller_receivable_breakdown at capture time (never estimated).
    # NULL when PayPal has not reported it (e.g. PENDING captures).
    paypal_fee_minor: Mapped[int | None] = mapped_column(BigInteger)
    net_minor: Mapped[int | None] = mapped_column(BigInteger)
    approval_id: Mapped[str | None] = mapped_column(ForeignKey("approvals.id"))
    verification_run_id: Mapped[str | None] = mapped_column(ForeignKey("verification_runs.id"))
    policy_decision_id: Mapped[str | None] = mapped_column(String(36))

    authorization: Mapped[PaymentAuthorization] = relationship(back_populates="capture")
    refunds: Mapped[list[PaymentRefund]] = relationship(back_populates="capture")


class PaymentRefund(IdMixin, TimestampMixin, Base):
    __tablename__ = "payment_refunds"
    __table_args__ = (CheckConstraint("amount_minor > 0", name="amount_positive"),)

    payment_capture_id: Mapped[str] = mapped_column(ForeignKey("payment_captures.id"))
    request_id: Mapped[str] = mapped_column(String(64), unique=True)
    paypal_refund_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[RefundStatus] = mapped_column(enum_col(RefundStatus, "refund_status"))
    paypal_status: Mapped[str | None] = mapped_column(String(40))
    note: Mapped[str | None] = mapped_column(String(255))
    # PayPal-reported seller_payable_breakdown: fee PayPal returned to the merchant, and
    # the net amount debited from the merchant for this refund. NULL until reported.
    paypal_fee_returned_minor: Mapped[int | None] = mapped_column(BigInteger)
    merchant_debit_minor: Mapped[int | None] = mapped_column(BigInteger)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    last_paypal_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    requested_by: Mapped[str] = mapped_column(String(200))

    capture: Mapped[PaymentCapture] = relationship(back_populates="refunds")


class WebhookEvent(IdMixin, Base):
    """Every webhook delivery we receive, verified or not. `paypal_event_id` is unique,
    which is the replay guard: a redelivered event is recorded once and processed once."""

    __tablename__ = "webhook_events"

    paypal_event_id: Mapped[str] = mapped_column(String(64), unique=True)
    event_type: Mapped[str] = mapped_column(String(80))
    resource_id: Mapped[str | None] = mapped_column(String(64), index=True)
    transmission_id: Mapped[str | None] = mapped_column(String(64))
    verification_status: Mapped[str] = mapped_column(String(20))  # SUCCESS / FAILURE / SKIPPED
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_result: Mapped[str | None] = mapped_column(String(200))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class IdempotencyRecord(IdMixin, Base):
    """Client-supplied Idempotency-Key for our own mutating endpoints."""

    __tablename__ = "idempotency_records"

    key: Mapped[str] = mapped_column(String(128))
    scope: Mapped[str] = mapped_column(String(80))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int | None] = mapped_column()
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    __table_args__ = (Index("uq_idempotency_scope_key", "scope", "key", unique=True),)
