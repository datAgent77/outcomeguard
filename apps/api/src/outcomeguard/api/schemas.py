"""Response shapes. Money is exposed as decimal strings plus currency; never floats."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from outcomeguard.models import (
    PaymentCapture,
    PaymentOrder,
    PaymentRefund,
)
from outcomeguard.money import Money


class RefundOut(BaseModel):
    id: str
    environment: str
    paypal_refund_id: str | None
    amount: str
    currency: str
    status: str
    paypal_status: str | None
    error: dict | None
    created_at: datetime

    @classmethod
    def of(cls, r: PaymentRefund, environment: str) -> RefundOut:
        return cls(
            id=r.id,
            environment=environment,
            paypal_refund_id=r.paypal_refund_id,
            amount=Money(r.amount_minor, r.currency).to_decimal_str(),
            currency=r.currency,
            status=r.status.value,
            paypal_status=r.paypal_status,
            error=r.error,
            created_at=r.created_at,
        )


class CaptureOut(BaseModel):
    id: str
    environment: str
    paypal_capture_id: str | None
    amount: str
    currency: str
    status: str
    paypal_status: str | None
    refundable_balance: str
    error: dict | None
    refunds: list[RefundOut]
    created_at: datetime

    @classmethod
    def of(cls, c: PaymentCapture, environment: str, refundable: Money) -> CaptureOut:
        return cls(
            id=c.id,
            environment=environment,
            paypal_capture_id=c.paypal_capture_id,
            amount=Money(c.amount_minor, c.currency).to_decimal_str(),
            currency=c.currency,
            status=c.status.value,
            paypal_status=c.paypal_status,
            refundable_balance=refundable.to_decimal_str(),
            error=c.error,
            refunds=[RefundOut.of(r, environment) for r in c.refunds],
            created_at=c.created_at,
        )


class AuthorizationOut(BaseModel):
    id: str
    paypal_authorization_id: str
    amount: str
    currency: str
    status: str
    paypal_status: str
    payee_merchant_id: str | None
    expires_at: datetime | None
    honor_period_ends_at: datetime | None


class OrderOut(BaseModel):
    id: str
    environment: str
    simulated: bool
    reference: str
    paypal_order_id: str | None
    amount: str
    currency: str
    status: str
    paypal_status: str | None
    approval_url: str | None
    authorization: AuthorizationOut | None = None
    capture: CaptureOut | None = None
    created_at: datetime

    @classmethod
    def of(cls, o: PaymentOrder, refundable: Money | None = None) -> OrderOut:
        env = o.environment.value
        a = o.authorization
        auth = None
        cap = None
        if a:
            auth = AuthorizationOut(
                id=a.id,
                paypal_authorization_id=a.paypal_authorization_id,
                amount=Money(a.amount_minor, a.currency).to_decimal_str(),
                currency=a.currency,
                status=a.status.value,
                paypal_status=a.paypal_status,
                payee_merchant_id=a.payee_merchant_id,
                expires_at=a.expires_at,
                honor_period_ends_at=a.honor_period_ends_at,
            )
            if a.capture:
                cap = CaptureOut.of(a.capture, env, refundable or Money(0, a.currency))
        return cls(
            id=o.id,
            environment=env,
            simulated=env == "simulated",
            reference=o.reference,
            paypal_order_id=o.paypal_order_id,
            amount=Money(o.amount_minor, o.currency).to_decimal_str(),
            currency=o.currency,
            status=o.status.value,
            paypal_status=o.paypal_status,
            approval_url=o.approval_url,
            authorization=auth,
            capture=cap,
            created_at=o.created_at,
        )


class CreateOrderIn(BaseModel):
    amount: str = Field(pattern=r"^\d{1,7}(\.\d{1,2})?$", examples=["500.00"])
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    description: str = Field(default="OutcomeGuard sandbox proof", max_length=127)


class RefundIn(BaseModel):
    amount: str = Field(pattern=r"^\d{1,7}(\.\d{1,2})?$", examples=["50.00"])
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")
    note: str | None = Field(default=None, max_length=255)


class CaptureIn(BaseModel):
    note_to_payer: str | None = Field(default=None, max_length=255)
