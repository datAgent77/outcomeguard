"""Payment lifecycle rules against the scriptable fake gateway (mocked PayPal)."""

import threading
from datetime import timedelta

import pytest

from outcomeguard.models import (
    AuthorizationStatus,
    CaptureStatus,
    MerchantAccount,
    OrderStatus,
    PaymentAuthorization,
    PaymentEnvironment,
    RefundStatus,
)
from outcomeguard.models.base import utcnow
from outcomeguard.money import Money
from outcomeguard.paypal.gateway import PayPalAPIError, PayPalUncertainError
from outcomeguard.services.payments import PaymentRuleViolation, PaymentService, PaymentUncertain
from tests.conftest import USD500


def code(exc_info) -> str:
    return exc_info.value.code


# ---------------------------------------------------------------- happy path


def test_full_lifecycle(svc, approved_order, gateway):
    assert approved_order.environment is PaymentEnvironment.SIMULATED
    assert approved_order.approval_url.endswith(approved_order.paypal_order_id)

    auth = svc.authorize_order(approved_order.id, actor="buyer")
    assert auth.status is AuthorizationStatus.AUTHORIZED
    assert auth.paypal_authorization_id.startswith("SIM-")
    assert auth.expires_at and auth.honor_period_ends_at

    cap = svc.capture_authorization(auth.id, actor="approver")
    assert cap.status is CaptureStatus.COMPLETED
    assert cap.paypal_capture_id.startswith("SIM-")
    assert svc.refundable_balance(cap) == USD500

    r = svc.refund_capture(
        cap.id, amount=Money(5000, "USD"), idempotency_key="refund-0001", actor="m"
    )
    assert r.status is RefundStatus.COMPLETED
    assert svc.refundable_balance(svc._get(type(cap), cap.id)) == Money(45000, "USD")
    assert gateway.calls["capture_authorization"] == 1


# ------------------------------------------------------------ authorization


def test_authorize_requires_buyer_approval_read_from_paypal(svc, merchant):
    order = svc.create_order(merchant=merchant, amount=USD500, description="x", actor="t")
    with pytest.raises(PaymentRuleViolation) as ei:
        svc.authorize_order(order.id, actor="buyer")
    assert code(ei) == "ORDER_NOT_APPROVED"


def test_authorize_is_idempotent(svc, approved_order, gateway):
    a1 = svc.authorize_order(approved_order.id, actor="buyer")
    a2 = svc.authorize_order(approved_order.id, actor="buyer")
    assert a1.id == a2.id
    assert gateway.calls["authorize_order"] == 1


def test_authorize_adopts_paypal_authorization_after_lost_response(svc, approved_order, gateway):
    gateway.fail_after["authorize_order"] = PayPalUncertainError("timeout")
    with pytest.raises(PaymentUncertain):
        svc.authorize_order(approved_order.id, actor="buyer")
    # PayPal authorized; our retry reads the COMPLETED order and adopts the authorization.
    auth = svc.authorize_order(approved_order.id, actor="buyer")
    assert auth.status is AuthorizationStatus.AUTHORIZED
    assert gateway.calls["authorize_order"] == 1


def test_amount_mismatch_blocks_authorization(svc, approved_order, gateway):
    def tamper(resp):
        pu = resp["purchase_units"][0]
        pu["payments"]["authorizations"][0] = {
            **pu["payments"]["authorizations"][0],
            "amount": {"currency_code": "USD", "value": "5.00"},
        }
        return resp

    gateway.override["authorize_order"] = tamper
    with pytest.raises(PaymentRuleViolation) as ei:
        svc.authorize_order(approved_order.id, actor="buyer")
    assert code(ei) == "AMOUNT_MISMATCH"


def test_merchant_mismatch_blocks_authorization(svc, approved_order, session):
    m = session.get(MerchantAccount, approved_order.merchant_account_id)
    m.paypal_merchant_id = "SOMEONE-ELSE"
    session.commit()
    with pytest.raises(PaymentRuleViolation) as ei:
        svc.authorize_order(approved_order.id, actor="buyer")
    assert code(ei) == "MERCHANT_MISMATCH"


def test_cancelled_order_cannot_be_authorized(svc, merchant):
    order = svc.create_order(merchant=merchant, amount=USD500, description="x", actor="t")
    svc.mark_cancelled(order)
    with pytest.raises(PaymentRuleViolation) as ei:
        svc.authorize_order(order.id, actor="buyer")
    assert code(ei) == "ORDER_NOT_AUTHORIZABLE"


# ------------------------------------------------------------------ capture


def test_duplicate_capture_blocked(svc, capture, gateway):
    with pytest.raises(PaymentRuleViolation) as ei:
        svc.capture_authorization(capture.payment_authorization_id, actor="approver")
    assert code(ei) == "ALREADY_CAPTURED"
    assert gateway.calls["capture_authorization"] == 1


def test_concurrent_captures_move_money_once(make_session, authorization, gateway, settings):
    barrier = threading.Barrier(4)
    outcomes: list[str] = []

    def worker():
        s = make_session()
        svc = PaymentService(session=s, gateway=gateway, settings=settings)
        barrier.wait()
        try:
            svc.capture_authorization(authorization.id, actor="approver")
            outcomes.append("captured")
        except PaymentRuleViolation as exc:
            outcomes.append(exc.code)
        finally:
            s.close()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count("captured") == 1
    assert set(outcomes) - {"captured"} <= {"ALREADY_CAPTURED", "CAPTURE_IN_PROGRESS"}
    assert gateway.calls["capture_authorization"] == 1


def test_lost_capture_response_is_unknown_and_reconciled_without_double_capture(
    svc, authorization, gateway
):
    gateway.fail_after["capture_authorization"] = PayPalUncertainError("read timeout")
    with pytest.raises(PaymentUncertain) as ei:
        svc.capture_authorization(authorization.id, actor="approver")
    cap_id = ei.value.entity_id
    cap = svc._get(type(svc._get(PaymentAuthorization, authorization.id).capture), cap_id)
    assert cap.status is CaptureStatus.UNKNOWN

    # A second approval click must not start another capture.
    with pytest.raises(PaymentRuleViolation) as e2:
        svc.capture_authorization(authorization.id, actor="approver")
    assert code(e2) == "CAPTURE_IN_PROGRESS"

    cap = svc.reconcile_capture(cap_id)
    assert cap.status is CaptureStatus.COMPLETED
    # Read-first reconciliation: the existing PayPal capture is found on the order; no
    # second capture POST is sent.
    assert gateway.calls["capture_authorization"] == 1
    assert svc.last_reconcile_method == "read_existing_capture"
    assert len(gateway._captures) == 1  # PayPal captured exactly once


def test_rejected_capture_is_failed_and_retryable(svc, authorization, gateway):
    gateway.fail_before["capture_authorization"] = PayPalAPIError(
        422, {"name": "UNPROCESSABLE_ENTITY", "details": [{"issue": "INSTRUMENT_DECLINED"}]}, "d"
    )
    with pytest.raises(PayPalAPIError):
        svc.capture_authorization(authorization.id, actor="approver")
    auth = svc._get(PaymentAuthorization, authorization.id)
    assert auth.capture.status is CaptureStatus.FAILED
    assert auth.status is AuthorizationStatus.AUTHORIZED
    cap = svc.capture_authorization(authorization.id, actor="approver")
    assert cap.status is CaptureStatus.COMPLETED


def test_expired_authorization_blocks_capture(svc, authorization, session, gateway):
    auth = session.get(PaymentAuthorization, authorization.id)
    auth.expires_at = utcnow() - timedelta(minutes=1)
    session.commit()
    with pytest.raises(PaymentRuleViolation) as ei:
        svc.capture_authorization(authorization.id, actor="approver")
    assert code(ei) == "AUTHORIZATION_NOT_CAPTURABLE"
    assert gateway.calls["capture_authorization"] == 0


def test_voided_at_paypal_blocks_capture(svc, authorization, gateway):
    gateway.set_auth_status(authorization.paypal_authorization_id, "VOIDED")
    with pytest.raises(PaymentRuleViolation) as ei:
        svc.capture_authorization(authorization.id, actor="approver")
    assert code(ei) == "AUTHORIZATION_NOT_CAPTURABLE"


def test_capture_payee_mismatch_blocked(svc, authorization, session):
    auth = session.get(PaymentAuthorization, authorization.id)
    auth.payee_merchant_id = "ATTACKER"
    session.commit()
    with pytest.raises(PaymentRuleViolation) as ei:
        svc.capture_authorization(authorization.id, actor="approver")
    assert code(ei) == "MERCHANT_MISMATCH"


def test_capture_amount_tampered_by_paypal_response_is_not_marked_complete(
    svc, authorization, gateway
):
    gateway.override["capture_authorization"] = lambda r: {
        **r,
        "amount": {"currency_code": "USD", "value": "1.00"},
    }
    cap = svc.capture_authorization(authorization.id, actor="approver")
    assert cap.status is CaptureStatus.UNKNOWN
    assert "amount_mismatch" in cap.error


# ------------------------------------------------------------------- refund


def refund(svc, cap, value, key):
    return svc.refund_capture(
        cap.id, amount=Money.from_decimal_str(value, "USD"), idempotency_key=key, actor="merchant"
    )


def test_refund_cannot_exceed_balance(svc, capture):
    refund(svc, capture, "450.00", "refund-aaaa")
    with pytest.raises(PaymentRuleViolation) as ei:
        refund(svc, capture, "50.01", "refund-bbbb")
    assert code(ei) == "REFUND_EXCEEDS_BALANCE"
    r = refund(svc, capture, "50.00", "refund-cccc")
    assert r.status is RefundStatus.COMPLETED


def test_duplicate_refund_key_returns_original(svc, capture, gateway):
    r1 = refund(svc, capture, "50.00", "refund-dup1")
    r2 = refund(svc, capture, "50.00", "refund-dup1")
    assert r1.id == r2.id
    assert gateway.calls["refund_capture"] == 1


def test_refund_key_reuse_with_different_amount_rejected(svc, capture):
    refund(svc, capture, "50.00", "refund-dup2")
    with pytest.raises(PaymentRuleViolation) as ei:
        refund(svc, capture, "60.00", "refund-dup2")
    assert code(ei) == "IDEMPOTENCY_KEY_REUSED"


def test_refund_currency_mismatch(svc, capture):
    with pytest.raises(PaymentRuleViolation) as ei:
        svc.refund_capture(
            capture.id, amount=Money(100, "EUR"), idempotency_key="refund-eur1", actor="m"
        )
    assert code(ei) == "CURRENCY_MISMATCH"


def test_refund_requires_completed_capture(svc, authorization, gateway):
    gateway.fail_after["capture_authorization"] = PayPalUncertainError("timeout")
    with pytest.raises(PaymentUncertain) as ei:
        svc.capture_authorization(authorization.id, actor="approver")
    with pytest.raises(PaymentRuleViolation) as e2:
        svc.refund_capture(
            ei.value.entity_id, amount=Money(100, "USD"), idempotency_key="refund-unk1", actor="m"
        )
    assert code(e2) == "CAPTURE_NOT_REFUNDABLE"


def test_uncertain_refund_holds_balance(svc, capture, gateway):
    gateway.fail_before["refund_capture"] = PayPalUncertainError("timeout")
    with pytest.raises(PaymentUncertain):
        refund(svc, capture, "500.00", "refund-unk2")
    # The UNKNOWN refund still reserves the balance: no second full refund possible.
    with pytest.raises(PaymentRuleViolation) as ei:
        refund(svc, capture, "1.00", "refund-unk3")
    assert code(ei) == "REFUND_EXCEEDS_BALANCE"


def test_concurrent_refunds_never_exceed_capture(make_session, capture, gateway, settings):
    barrier = threading.Barrier(5)
    done: list[str] = []

    def worker(i: int):
        s = make_session()
        svc = PaymentService(session=s, gateway=gateway, settings=settings)
        barrier.wait()
        try:
            refund(svc, capture, "150.00", f"refund-conc-{i}")
            done.append("ok")
        except PaymentRuleViolation as exc:
            done.append(exc.code)
        finally:
            s.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert done.count("ok") == 3  # 3 x 150 = 450 <= 500; a 4th would exceed
    assert done.count("REFUND_EXCEEDS_BALANCE") == 2


def test_order_status_progression(svc, approved_order, session):
    svc.sync_order(approved_order)
    assert approved_order.status is OrderStatus.APPROVED
    svc.authorize_order(approved_order.id, actor="buyer")
    assert svc._get(type(approved_order), approved_order.id).status is OrderStatus.AUTHORIZED


def test_paypal_status_mirrors_are_read_back_after_money_moves(svc, capture):
    # Found in the live Sandbox run 2026-10-15: parents kept stale paypal_status.
    auth = svc._get(PaymentAuthorization, capture.payment_authorization_id)
    assert auth.paypal_status == "CAPTURED"
    svc.refund_capture(
        capture.id, amount=Money(200, "USD"), idempotency_key="mirror-ref-1", actor="m"
    )
    cap = svc._get(type(capture), capture.id)
    assert cap.paypal_status == "PARTIALLY_REFUNDED"
    assert cap.status is CaptureStatus.COMPLETED


# ------------------------------------------------- financial reporting accuracy


def _with_breakdown(resp, key, **values):
    b = {k: {"currency_code": "USD", "value": v} for k, v in values.items()}
    return {**resp, key: b}


def test_settlement_uses_only_paypal_reported_figures(svc, authorization, gateway):
    # Figures mirror the live Sandbox run of 2026-10-15.
    gateway.override["capture_authorization"] = lambda r: _with_breakdown(
        r,
        "seller_receivable_breakdown",
        gross_amount="10.00",
        paypal_fee="0.84",
        net_amount="9.16",
    )
    gateway.override["refund_capture"] = lambda r: _with_breakdown(
        r,
        "seller_payable_breakdown",
        gross_amount="2.00",
        paypal_fee="0.00",
        net_amount="2.00",
        total_refunded_amount="2.00",
    )
    from outcomeguard.services.payments import settlement_summary

    cap = svc.capture_authorization(authorization.id, actor="approver")
    assert (cap.paypal_fee_minor, cap.net_minor) == (84, 916)
    svc.refund_capture(cap.id, amount=Money(200, "USD"), idempotency_key="fin-ref-1", actor="m")
    s = settlement_summary(svc._get(type(cap), cap.id))
    assert s.paypal_fee == 84 and s.net_at_capture == 916
    assert s.refunded_gross == 200 and s.refund_fees_returned == 0
    assert s.refund_merchant_debits == 200
    assert s.net_after_refunds == 716  # derived: 9.16 - 2.00, not a PayPal balance


def test_missing_breakdown_is_never_estimated(svc, capture):
    from outcomeguard.services.payments import settlement_summary

    s = settlement_summary(capture)
    assert s.gross_captured == 50000
    assert s.paypal_fee is None and s.net_at_capture is None and s.net_after_refunds is None


def test_net_after_refunds_unknown_while_refund_in_flight(svc, authorization, gateway):
    gateway.override["capture_authorization"] = lambda r: _with_breakdown(
        r,
        "seller_receivable_breakdown",
        gross_amount="500.00",
        paypal_fee="17.94",
        net_amount="482.06",
    )
    from outcomeguard.services.payments import settlement_summary

    cap = svc.capture_authorization(authorization.id, actor="approver")
    gateway.fail_before["refund_capture"] = PayPalUncertainError("timeout")
    with pytest.raises(PaymentUncertain):
        svc.refund_capture(cap.id, amount=Money(100, "USD"), idempotency_key="fin-ref-2", actor="m")
    s = settlement_summary(svc._get(type(cap), cap.id))
    assert s.refunds_in_flight == 100 and s.net_after_refunds is None


def test_reconcile_replays_same_request_id_only_when_paypal_has_no_capture(
    svc, authorization, gateway
):
    # The request never reached PayPal (lost before processing): nothing to read, so the
    # SAME PayPal-Request-Id is replayed — idempotent, still at most one capture.
    gateway.fail_before["capture_authorization"] = PayPalUncertainError("connect timeout")
    with pytest.raises(PaymentUncertain) as ei:
        svc.capture_authorization(authorization.id, actor="approver")
    cap = svc.reconcile_capture(ei.value.entity_id)
    assert cap.status is CaptureStatus.COMPLETED
    assert svc.last_reconcile_method == "replay_same_request_id"
    ids = gateway.request_ids["capture_authorization"]
    assert len(ids) == 2 and ids[0] == ids[1] and len(gateway._captures) == 1
