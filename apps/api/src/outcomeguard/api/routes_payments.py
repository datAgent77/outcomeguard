"""P02 sandbox-proof endpoints (operator-only) and the PayPal buyer return pages.

The `/dev/payments` router drives the raw payment lifecycle for the PayPal feasibility
gate. In the product flow (P06) capture and refund are reachable only through the policy
engine and human approval; these endpoints exist to prove the PayPal integration in
isolation and are disabled unless ENABLE_SANDBOX_PROOF=true.
"""

from __future__ import annotations

import html

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from outcomeguard.api.deps import OperatorDep, PaymentServiceDep, SessionDep, SettingsDep
from outcomeguard.api.schemas import (
    CaptureIn,
    CaptureOut,
    CreateOrderIn,
    OrderOut,
    RefundIn,
    RefundOut,
)
from outcomeguard.config import PaymentMode
from outcomeguard.models import PaymentCapture, PaymentOrder
from outcomeguard.models.audit import verify_chain
from outcomeguard.money import Money, MoneyError
from outcomeguard.paypal.rehearsal import RehearsalPayPalGateway
from outcomeguard.services.merchants import ensure_demo_merchant

dev = APIRouter(prefix="/dev/payments", tags=["sandbox-proof"])
public = APIRouter(tags=["paypal-return"])


def _order_out(svc, order: PaymentOrder) -> OrderOut:
    cap = order.authorization.capture if order.authorization else None
    return OrderOut.of(order, svc.refundable_balance(cap) if cap else None)


def _money(amount: str, currency: str) -> Money:
    try:
        return Money.from_decimal_str(amount, currency)
    except MoneyError as exc:
        raise HTTPException(422, str(exc)) from exc


@dev.post("/orders", response_model=OrderOut)
def create_order(body: CreateOrderIn, svc: PaymentServiceDep, actor: OperatorDep) -> OrderOut:
    merchant = ensure_demo_merchant(svc.session, svc.environment)
    order = svc.create_order(
        merchant=merchant,
        amount=_money(body.amount, body.currency),
        description=body.description,
        actor=actor,
    )
    return _order_out(svc, order)


@dev.get("/orders", response_model=list[OrderOut])
def list_orders(svc: PaymentServiceDep, _: OperatorDep) -> list[OrderOut]:
    orders = svc.session.scalars(select(PaymentOrder).order_by(PaymentOrder.created_at.desc()))
    return [_order_out(svc, o) for o in orders]


@dev.get("/orders/{order_id}", response_model=OrderOut)
def get_order(
    order_id: str, svc: PaymentServiceDep, actor: OperatorDep, sync: bool = True
) -> OrderOut:
    order = svc._get(PaymentOrder, order_id)
    if sync and order.paypal_order_id and not order.authorization:
        order = svc.sync_order(order, actor=actor)
    return _order_out(svc, order)


@dev.post("/orders/{order_id}/retry-create", response_model=OrderOut)
def retry_create(order_id: str, svc: PaymentServiceDep, _: OperatorDep) -> OrderOut:
    return _order_out(svc, svc.retry_create_order(svc._get(PaymentOrder, order_id)))


@dev.post("/orders/{order_id}/authorize", response_model=OrderOut)
def authorize(order_id: str, svc: PaymentServiceDep, actor: OperatorDep) -> OrderOut:
    svc.authorize_order(order_id, actor=actor)
    return _order_out(svc, svc._get(PaymentOrder, order_id))


@dev.post("/authorizations/{auth_id}/capture", response_model=CaptureOut)
def capture(
    auth_id: str, body: CaptureIn, svc: PaymentServiceDep, actor: OperatorDep
) -> CaptureOut:
    cap = svc.capture_authorization(auth_id, actor=actor, note_to_payer=body.note_to_payer)
    return CaptureOut.of(cap, svc.environment.value, svc.refundable_balance(cap))


@dev.post("/captures/{capture_id}/reconcile", response_model=CaptureOut)
def reconcile_capture(capture_id: str, svc: PaymentServiceDep, _: OperatorDep) -> CaptureOut:
    cap = svc.reconcile_capture(capture_id)
    return CaptureOut.of(cap, svc.environment.value, svc.refundable_balance(cap))


@dev.post("/captures/{capture_id}/refunds", response_model=RefundOut)
def refund(
    capture_id: str, body: RefundIn, svc: PaymentServiceDep, actor: OperatorDep
) -> RefundOut:
    cap = svc._get(PaymentCapture, capture_id)
    r = svc.refund_capture(
        capture_id,
        amount=_money(body.amount, cap.currency),
        idempotency_key=body.idempotency_key,
        actor=actor,
        note=body.note,
    )
    return RefundOut.of(r, svc.environment.value)


@dev.post("/refunds/{refund_id}/reconcile", response_model=RefundOut)
def reconcile_refund(refund_id: str, svc: PaymentServiceDep, _: OperatorDep) -> RefundOut:
    return RefundOut.of(svc.reconcile_refund(refund_id), svc.environment.value)


@dev.get("/audit/verify")
def audit_verify(session: SessionDep, _: OperatorDep) -> dict:
    ok, bad = verify_chain(session)
    return {"chain_intact": ok, "first_bad_seq": bad}


# ---- buyer-facing redirects (UX only — never trusted as proof of payment) ----------


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
        f"<title>{html.escape(title)}</title><body style='font:16px system-ui;max-width:40rem;"
        f"margin:3rem auto;padding:0 1rem'><h1>{html.escape(title)}</h1>{body}</body>"
    )


@public.get("/paypal/return", response_class=HTMLResponse)
def paypal_return(token: str, svc: PaymentServiceDep) -> HTMLResponse:
    order = svc.session.scalar(select(PaymentOrder).where(PaymentOrder.paypal_order_id == token))
    if not order:
        raise HTTPException(404, "unknown order")
    # The redirect only tells us *which* order to look at; status comes from PayPal.
    order = svc.sync_order(order, actor="buyer-return")
    label = (
        " (SIMULATED — rehearsal mode, not PayPal)"
        if order.environment.value == "simulated"
        else ""
    )
    return _page(
        "Payment approval received",
        f"<p>PayPal reports order <code>{html.escape(order.paypal_order_id or '')}</code> as "
        f"<b>{html.escape(order.paypal_status or 'unknown')}</b>{label}.</p>"
        "<p>No money has moved. The funds are authorized (held) only after OutcomeGuard "
        "authorizes the order server-side, and captured only after verification and human "
        "approval. You can close this tab.</p>",
    )


@public.get("/paypal/cancel", response_class=HTMLResponse)
def paypal_cancel(token: str, svc: PaymentServiceDep) -> HTMLResponse:
    order = svc.session.scalar(select(PaymentOrder).where(PaymentOrder.paypal_order_id == token))
    if order:
        svc.mark_cancelled(order)
    return _page("Payment cancelled", "<p>No funds were authorized. You can close this tab.</p>")


@public.get("/rehearsal/approve/{order_id}", response_class=HTMLResponse)
def rehearsal_page(order_id: str, settings: SettingsDep) -> HTMLResponse:
    if settings.payment_mode is not PaymentMode.REHEARSAL:
        raise HTTPException(404, "not found")
    oid = html.escape(order_id)
    return _page(
        "SIMULATED PayPal approval",
        "<p style='background:#fff3cd;padding:.75rem;border-radius:.5rem'><b>Rehearsal mode.</b> "
        "This is not PayPal. No account is charged and no PayPal object exists.</p>"
        f"<p>Simulated order <code>{oid}</code>.</p>"
        f"<form method=post action='/rehearsal/approve/{oid}'><button>Approve (simulated)</button>"
        f"</form><form method=get action='/paypal/cancel'><input type=hidden name=token value='{oid}'>"
        "<button>Cancel</button></form>",
    )


@public.post("/rehearsal/approve/{order_id}")
def rehearsal_approve(order_id: str, request: Request, svc: PaymentServiceDep) -> RedirectResponse:
    if not isinstance(svc.gateway, RehearsalPayPalGateway):
        raise HTTPException(404, "not found")
    svc.gateway.simulate_buyer_approval(order_id)
    return RedirectResponse(f"/paypal/return?token={order_id}", status_code=303)
