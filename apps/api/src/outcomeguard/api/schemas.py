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
from outcomeguard.services.payments import SettlementSummary, settlement_summary


def _amt(minor: int | None, currency: str) -> str | None:
    return None if minor is None else Money(minor, currency).to_decimal_str()


class RefundOut(BaseModel):
    id: str
    environment: str
    paypal_refund_id: str | None
    amount: str
    currency: str
    status: str
    paypal_status: str | None
    paypal_fee_returned: str | None = Field(
        description="Fee PayPal returned to the merchant for this refund (PayPal-reported)."
    )
    merchant_debit: str | None = Field(
        description="Net amount debited from the merchant for this refund (PayPal-reported)."
    )
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
            paypal_fee_returned=_amt(r.paypal_fee_returned_minor, r.currency),
            merchant_debit=_amt(r.merchant_debit_minor, r.currency),
            error=r.error,
            created_at=r.created_at,
        )


class SettlementOut(BaseModel):
    """Every figure is PayPal-reported except `net_after_refunds_derived`, which is
    arithmetic on PayPal-reported figures — not a balance read from PayPal. Null means
    PayPal did not report the input; nothing is estimated."""

    currency: str
    gross_captured: str
    paypal_fee: str | None
    net_at_capture: str | None
    refunded_gross: str
    refund_fees_returned: str | None
    refund_merchant_debits: str | None
    net_after_refunds_derived: str | None
    refunds_in_flight: str
    source: str = "paypal_reported_breakdowns"

    @classmethod
    def of(cls, s: SettlementSummary) -> SettlementOut:
        c = s.currency
        return cls(
            currency=c,
            gross_captured=Money(s.gross_captured, c).to_decimal_str(),
            paypal_fee=_amt(s.paypal_fee, c),
            net_at_capture=_amt(s.net_at_capture, c),
            refunded_gross=Money(s.refunded_gross, c).to_decimal_str(),
            refund_fees_returned=_amt(s.refund_fees_returned, c),
            refund_merchant_debits=_amt(s.refund_merchant_debits, c),
            net_after_refunds_derived=_amt(s.net_after_refunds, c),
            refunds_in_flight=Money(s.refunds_in_flight, c).to_decimal_str(),
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
    settlement: SettlementOut
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
            settlement=SettlementOut.of(settlement_summary(c)),
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
