"""P08 resolution, refund and void governance with the MOCKED PayPal gateway (fault
injection). Not real PayPal evidence — see docs/INTEGRATION_PROOF.md for Sandbox runs."""

from __future__ import annotations

import threading
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from outcomeguard.auth import Forbidden, load_principal
from outcomeguard.models import (
    ApprovalChoice,
    AuditEvent,
    AuthorizationStatus,
    AuthorizationVoid,
    CaptureStatus,
    CaseStatus,
    FinancialPolicyDecision,
    PaymentAuthorization,
    PaymentCapture,
    PaymentRefund,
    ProposalStatus,
    ReasonCategory,
    RefundStatus,
    Remedy,
    ResolutionCase,
    ResolutionEvent,
    ReviewDecision,
    User,
    UserKind,
    VoidStatus,
)
from outcomeguard.models.identity import OrganizationMembership, Role
from outcomeguard.money import Money
from outcomeguard.paypal.gateway import PayPalAPIError, PayPalUncertainError
from outcomeguard.services.governance import Governance, GovernanceError, evidence_snapshot_sha256
from outcomeguard.services.payments import PaymentRuleViolation, PaymentService, PaymentUncertain
from outcomeguard.services.resolution import Resolution
from tests.govhelpers import (
    authorized_payment,
    completed_run,
    locked_agreement_with_manual,
    manual_criterion_id,
)

USD = "USD"


@pytest.fixture
def gov(session, svc):
    return Governance(session=session, payments=svc)


@pytest.fixture
def res(session, svc):
    return Resolution(session=session, payments=svc)


def _review_and_capture(gov, who, agreement, auth, run):
    gov.record_review(
        who("rita"),
        run_id=run.id,
        criterion_id=manual_criterion_id(gov.session, agreement),
        decision=ReviewDecision.SATISFIED,
        rationale="Checked keyboard navigation, contrast and labels (WCAG AA).",
        evidence_snapshot_sha256_seen=evidence_snapshot_sha256(gov.session, run),
    )
    p = gov.preview(who("bob"), auth.id)
    a = gov.approve_capture(who("bob"), auth.id, p.inputs_sha256)
    return gov.capture(who("bob"), auth.id, a.id)


@pytest.fixture
def world(session, svc, gateway, ids, who, gov, tmp_path):
    """Locked agreement → authorized → verified + reviewed → captured $500 (simulated)."""
    agreement = locked_agreement_with_manual(session, ids, who)
    auth = authorized_payment(session, svc, gateway, agreement)
    run = completed_run(session, who, agreement, tmp_path, form_ok=True)
    cap = _review_and_capture(gov, who, agreement, auth, run)
    assert cap.status is CaptureStatus.COMPLETED
    session.expire_all()
    return agreement, auth, cap, run


def codes(ei) -> list[str]:
    return [r["code"] for r in ei.value.reasons]


def open_refund_case(res, who, agreement, amount="100.00", **kw):
    return res.open_case(
        who("alice"),
        agreement,
        reason_category=ReasonCategory.DEFECT_AFTER_ACCEPTANCE,
        description="The contact form stopped sending messages after launch.",
        requested_remedy=Remedy.PARTIAL_REFUND,
        requested_amount=Money.from_decimal_str(amount, USD),
        criterion_ids=kw.get("criterion_ids", []),
        artifact_ids=kw.get("artifact_ids", []),
    )


def agree_refund(res, who, case, amount="100.00", remedy=Remedy.PARTIAL_REFUND):
    case = res.session.get(ResolutionCase, case.id)
    p = res.submit_proposal(
        who("alice"),
        case.id,
        expected_revision=case.revision,
        remedy=remedy,
        amount=Money.from_decimal_str(amount, USD) if amount else None,
        rationale="Partial refund for the broken contact form.",
        evidence_refs=[],
        approve=True,
    )
    res.decide(
        who("maya"),
        case.id,
        p.id,
        choice=ApprovalChoice.APPROVE,
        content_sha256=p.content_sha256,
        note=None,
    )
    res.session.expire_all()
    return res.session.get(type(p), p.id)


# ================================================================ opening


def test_open_case_binds_agreement_capture_and_evidence(res, who, world, session):
    agreement, auth, cap, run = world
    crit = manual_criterion_id(session, agreement)
    case = open_refund_case(res, who, agreement, criterion_ids=[crit])
    assert case.status is CaseStatus.OPEN and case.payment_capture_id == cap.id
    snap = case.evidence_snapshot
    assert snap["verification_run"]["id"] == run.id
    assert snap["verification_run"]["evidence_snapshot_sha256"] == evidence_snapshot_sha256(
        session, run
    )
    assert snap["criteria"][0]["id"] == crit
    assert snap["manual_reviews"][0]["decision"] == "satisfied"
    assert snap["payment"]["paypal_capture_id"] == cap.paypal_capture_id
    assert snap["agreement_content_hash"] == case.agreement_content_hash
    # Opening moves no money.
    assert session.scalars(select(PaymentRefund)).all() == []


def test_evidence_snapshot_and_case_facts_are_immutable(res, who, world, session):
    case = open_refund_case(res, who, world[0])
    with pytest.raises(DBAPIError):
        session.execute(
            text("UPDATE resolution_cases SET evidence_snapshot = '{}'::jsonb WHERE id = :i"),
            {"i": case.id},
        )
    session.rollback()
    with pytest.raises(DBAPIError):
        session.execute(text("DELETE FROM resolution_events WHERE case_id = :i"), {"i": case.id})
    session.rollback()


def test_invalid_evidence_references_are_rejected(res, who, world):
    with pytest.raises(GovernanceError) as ei:
        open_refund_case(res, who, world[0], artifact_ids=["not-an-artifact"])
    assert ei.value.code == "EVIDENCE_REFERENCE_INVALID"


def test_one_live_case_per_capture(res, who, world):
    open_refund_case(res, who, world[0])
    with pytest.raises(GovernanceError) as ei:
        open_refund_case(res, who, world[0])
    assert ei.value.code == "CASE_ALREADY_OPEN"


def test_only_buyer_finance_humans_open_cases_and_outsiders_get_404(res, who, world, session, ids):
    agreement = world[0]
    with pytest.raises(Forbidden):
        open_refund_case.__wrapped__ if False else res.open_case(
            who("maya"),
            agreement,
            reason_category=ReasonCategory.OTHER,
            description="provider cannot open",
            requested_remedy=Remedy.OTHER,
            requested_amount=None,
            criterion_ids=[],
            artifact_ids=[],
        )
    case = open_refund_case(res, who, agreement)
    with pytest.raises(GovernanceError) as ei:
        res.get(who("eve"), case.id)
    assert ei.value.http_status == 404


def bot(session, ids):
    u = User(email="ai-assistant@system.local", display_name="AI", kind=UserKind.SERVICE)
    session.add(u)
    session.flush()
    for org in (ids.buyer_org_id, ids.merchant.organization_id):
        for role in (Role.BUYER_APPROVER,) if org == ids.buyer_org_id else (Role.MERCHANT_ADMIN,):
            session.add(OrganizationMembership(organization_id=org, user_id=u.id, role=role))
    session.commit()
    return load_principal(session, u.id)


def test_service_account_cannot_propose_approve_refund_or_void(res, who, world, session, ids):
    agreement, auth, cap, run = world
    case = open_refund_case(res, who, agreement)
    ai = bot(session, ids)
    with pytest.raises(Forbidden, match="human"):
        res.submit_proposal(
            ai,
            case.id,
            expected_revision=case.revision,
            remedy=Remedy.PARTIAL_REFUND,
            amount=Money(1000, USD),
            rationale="AI suggests a refund",
            evidence_refs=[],
            approve=True,
        )
    p = res.submit_proposal(
        who("alice"),
        case.id,
        expected_revision=res.session.get(ResolutionCase, case.id).revision,
        remedy=Remedy.PARTIAL_REFUND,
        amount=Money(1000, USD),
        rationale="Buyer proposal text",
        evidence_refs=[],
        approve=True,
    )
    with pytest.raises(Forbidden, match="human"):
        res.decide(
            ai,
            case.id,
            p.id,
            choice=ApprovalChoice.APPROVE,
            content_sha256=p.content_sha256,
            note=None,
        )
    with pytest.raises(Forbidden, match="human"):
        res.execute_refund(ai, case.id, p.id, "ai-key-0001")


# ============================================================ negotiation


def test_provider_reply_moves_case_under_review_and_rework_closes_without_money(
    res, who, world, session
):
    agreement, auth, cap, run = world
    case = open_refund_case(res, who, agreement)
    res.post_message(who("maya"), case.id, "We will fix the form today.")
    case = session.get(ResolutionCase, case.id)
    assert case.status is CaseStatus.UNDER_REVIEW
    p = res.submit_proposal(
        who("maya"),
        case.id,
        expected_revision=case.revision,
        remedy=Remedy.REWORK,
        amount=None,
        rationale="Rework the form and re-verify",
        evidence_refs=[],
        approve=True,
    )
    res.decide(
        who("bob"),
        case.id,
        p.id,
        choice=ApprovalChoice.APPROVE,
        content_sha256=p.content_sha256,
        note=None,
    )
    session.expire_all()
    assert session.get(ResolutionCase, case.id).status is CaseStatus.AGREED
    res.close(who("maya"), case.id, outcome="resolved", reason="Rework delivered")
    session.expire_all()
    c = session.get(ResolutionCase, case.id)
    assert c.status is CaseStatus.RESOLVED and c.outcome == "resolved"
    assert session.scalars(select(PaymentRefund)).all() == []
    assert (
        session.scalar(select(AuditEvent).where(AuditEvent.action == "resolution.closed"))
        is not None
    )


def test_stale_and_superseded_approvals_are_rejected(res, who, world, session):
    case = open_refund_case(res, who, world[0])
    p1 = res.submit_proposal(
        who("alice"),
        case.id,
        expected_revision=1,
        remedy=Remedy.PARTIAL_REFUND,
        amount=Money(15000, USD),
        rationale="First offer from the buyer",
        evidence_refs=[],
        approve=True,
    )
    with pytest.raises(GovernanceError) as ei:  # optimistic concurrency on the case
        res.submit_proposal(
            who("maya"),
            case.id,
            expected_revision=1,
            remedy=Remedy.PARTIAL_REFUND,
            amount=Money(10000, USD),
            rationale="Counter offer",
            evidence_refs=[],
            approve=True,
        )
    assert ei.value.code == "CASE_CHANGED"
    rev = session.get(ResolutionCase, case.id).revision
    p2 = res.submit_proposal(
        who("maya"),
        case.id,
        expected_revision=rev,
        remedy=Remedy.PARTIAL_REFUND,
        amount=Money(10000, USD),
        rationale="Counter offer",
        evidence_refs=[],
        approve=True,
    )
    session.expire_all()
    assert session.get(type(p1), p1.id).status is ProposalStatus.SUPERSEDED
    with pytest.raises(GovernanceError) as ei:
        res.decide(
            who("maya"),
            case.id,
            p1.id,
            choice=ApprovalChoice.APPROVE,
            content_sha256=p1.content_sha256,
            note=None,
        )
    assert ei.value.code == "APPROVAL_STALE"
    with pytest.raises(GovernanceError) as ei:
        res.decide(
            who("bob"),
            case.id,
            p2.id,
            choice=ApprovalChoice.APPROVE,
            content_sha256="0" * 64,
            note=None,
        )
    assert ei.value.code == "APPROVAL_STALE"
    with pytest.raises(GovernanceError) as ei:  # one decision per side
        res.decide(
            who("maya"),
            case.id,
            p2.id,
            choice=ApprovalChoice.APPROVE,
            content_sha256=p2.content_sha256,
            note=None,
        )
    assert ei.value.code == "ALREADY_DECIDED"


def test_provider_only_role_cannot_approve_money(res, who, world, session, ids):
    pat = User(email="pat@northwind.demo", display_name="Pat (provider)", kind=UserKind.HUMAN)
    session.add(pat)
    session.flush()
    session.add(
        OrganizationMembership(
            organization_id=ids.merchant.organization_id, user_id=pat.id, role=Role.PROVIDER
        )
    )
    session.commit()
    case = open_refund_case(res, who, world[0])
    p = res.submit_proposal(
        who("alice"),
        case.id,
        expected_revision=1,
        remedy=Remedy.PARTIAL_REFUND,
        amount=Money(5000, USD),
        rationale="Buyer proposal text",
        evidence_refs=[],
        approve=True,
    )
    with pytest.raises(Forbidden):
        res.decide(
            load_principal(session, pat.id),
            case.id,
            p.id,
            choice=ApprovalChoice.APPROVE,
            content_sha256=p.content_sha256,
            note=None,
        )


def test_rejection_returns_to_review_and_missing_approval_blocks_refund(
    res, who, world, session, gateway
):
    case = open_refund_case(res, who, world[0])
    p = res.submit_proposal(
        who("alice"),
        case.id,
        expected_revision=1,
        remedy=Remedy.PARTIAL_REFUND,
        amount=Money(10000, USD),
        rationale="Buyer proposal text",
        evidence_refs=[],
        approve=True,
    )
    with pytest.raises(GovernanceError) as ei:
        res.execute_refund(who("alice"), case.id, p.id, "refund-key-0001")
    assert ei.value.code == "POLICY_NOT_SATISFIED"
    assert {"RESOLUTION_NOT_AGREED", "APPROVAL_MISSING"} <= set(codes(ei))
    res.decide(
        who("maya"),
        case.id,
        p.id,
        choice=ApprovalChoice.REJECT,
        content_sha256=p.content_sha256,
        note="No",
    )
    session.expire_all()
    assert session.get(ResolutionCase, case.id).status is CaseStatus.UNDER_REVIEW
    assert gateway.calls["refund_capture"] == 0
    blocked = session.scalars(
        select(FinancialPolicyDecision).where(FinancialPolicyDecision.decision == "blocked")
    ).all()
    assert blocked  # the refusal is persisted as evidence


# ================================================================ refunds


def test_agreed_partial_refund_executes_once_and_resolves(res, who, world, session, gateway):
    agreement, auth, cap, run = world
    case = open_refund_case(res, who, agreement)
    p = agree_refund(res, who, case)
    assert p.status is ProposalStatus.ACCEPTED
    r = res.execute_refund(who("alice"), case.id, p.id, "refund-key-0001")
    assert r.status is RefundStatus.COMPLETED and r.amount_minor == 10000
    assert (r.resolution_case_id, r.proposal_id) == (case.id, p.id) and r.policy_decision_id
    again = res.execute_refund(who("alice"), case.id, p.id, "refund-key-0001")  # double click
    assert again.id == r.id and gateway.calls["refund_capture"] == 1
    with pytest.raises(GovernanceError) as ei:  # new key, same proposal
        res.execute_refund(who("alice"), case.id, p.id, "refund-key-0002")
    assert "REFUND_ALREADY_COMPLETED" in codes(ei)
    session.expire_all()
    c = session.get(ResolutionCase, case.id)
    assert c.status is CaseStatus.RESOLVED and c.outcome.startswith("refunded")
    assert session.get(type(p), p.id).status is ProposalStatus.EXECUTED
    assert res.payments.refundable_balance(session.get(PaymentCapture, cap.id)) == Money(40000, USD)
    kinds = [
        e.kind
        for e in session.scalars(select(ResolutionEvent).where(ResolutionEvent.case_id == case.id))
    ]
    assert "refund_requested" in kinds and "refund_completed" in kinds


def test_multiple_partials_then_full_refund_and_over_refund_blocked(res, who, world, session):
    agreement = world[0]
    c1 = open_refund_case(res, who, agreement)
    res.execute_refund(
        who("alice"), c1.id, agree_refund(res, who, c1, "100.00").id, "refund-key-0001"
    )
    c2 = open_refund_case(res, who, agreement, "300.00")
    res.execute_refund(
        who("maya"), c2.id, agree_refund(res, who, c2, "300.00").id, "refund-key-0002"
    )
    c3 = open_refund_case(res, who, agreement, "50.00")
    with pytest.raises(GovernanceError) as ei:  # only 100.00 left
        res.submit_proposal(
            who("alice"),
            c3.id,
            expected_revision=1,
            remedy=Remedy.PARTIAL_REFUND,
            amount=Money(10001, USD),
            rationale="Too much money",
            evidence_refs=[],
            approve=True,
        )
    assert ei.value.code == "REFUND_EXCEEDS_REMAINING"
    p = agree_refund(res, who, c3, None, remedy=Remedy.FULL_REFUND)
    assert p.amount_minor == 10000  # full = what remains, computed server-side
    res.execute_refund(who("alice"), c3.id, p.id, "refund-key-0003")
    cap = session.get(PaymentCapture, world[2].id)
    session.refresh(cap)
    assert res.payments.refundable_balance(cap).minor == 0


def test_wrong_currency_blocked(res, who, world):
    case = open_refund_case(res, who, world[0])
    with pytest.raises(GovernanceError) as ei:
        res.submit_proposal(
            who("alice"),
            case.id,
            expected_revision=1,
            remedy=Remedy.PARTIAL_REFUND,
            amount=Money(1000, "EUR"),
            rationale="Wrong currency offer",
            evidence_refs=[],
            approve=True,
        )
    assert ei.value.code == "CURRENCY_MISMATCH"


def test_dev_endpoint_and_direct_sql_cannot_bypass_refund_governance(svc, world, session):
    cap = world[2]
    with pytest.raises(PaymentRuleViolation) as ei:
        svc.refund_capture(
            cap.id, amount=Money(100, USD), idempotency_key="bypass-key-01", actor="operator"
        )
    assert ei.value.code == "POLICY_REQUIRED"
    with pytest.raises(DBAPIError, match="refund requires a resolution case"):
        session.execute(
            text(
                "INSERT INTO payment_refunds (id, payment_capture_id, request_id, amount_minor, currency,"
                " status, requested_by, created_at, updated_at) VALUES"
                " ('x', :c, 'r-x', 100, 'USD', 'requested', 'sql', now(), now())"
            ),
            {"c": cap.id},
        )
    session.rollback()


def test_concurrent_executions_of_one_proposal_move_money_once(
    make_session, res, who, world, gateway, settings
):
    case = open_refund_case(res, who, world[0])
    p = agree_refund(res, who, case)
    barrier = threading.Barrier(4)
    out: list[str] = []

    def worker(i: int):
        s = make_session()
        r = Resolution(
            session=s, payments=PaymentService(session=s, gateway=gateway, settings=settings)
        )
        principal = load_principal(s, who("alice").user_id)
        barrier.wait()
        try:
            r.execute_refund(principal, case.id, p.id, f"refund-conc-{i:04d}")
            out.append("ok")
        except GovernanceError as exc:
            out.append(exc.code)
        except Exception as exc:  # unique index race
            out.append(type(exc).__name__)
        finally:
            s.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert out.count("ok") == 1, out
    assert gateway.calls["refund_capture"] == 1


def test_paypal_rejection_fails_honestly_and_allows_a_retry(res, who, world, session, gateway):
    case = open_refund_case(res, who, world[0])
    p = agree_refund(res, who, case)
    gateway.fail_before["refund_capture"] = PayPalAPIError(
        422, {"name": "UNPROCESSABLE_ENTITY", "details": [{"issue": "TRANSACTION_REFUSED"}]}, "dbg"
    )
    with pytest.raises(PayPalAPIError):
        res.execute_refund(who("alice"), case.id, p.id, "refund-key-0001")
    session.expire_all()
    assert session.get(ResolutionCase, case.id).status is CaseStatus.ACTION_REQUIRED
    failed = session.scalar(select(PaymentRefund))
    assert failed.status is RefundStatus.FAILED
    r = res.execute_refund(who("alice"), case.id, p.id, "refund-key-0002")
    assert r.status is RefundStatus.COMPLETED
    session.expire_all()
    assert session.get(ResolutionCase, case.id).status is CaseStatus.RESOLVED


def test_paypal_5xx_is_unknown_and_replay_uses_the_same_request_id(
    res, who, world, session, gateway
):
    case = open_refund_case(res, who, world[0])
    p = agree_refund(res, who, case)
    gateway.fail_before["refund_capture"] = PayPalUncertainError("POST refund -> 503")
    with pytest.raises(PaymentUncertain):
        res.execute_refund(who("alice"), case.id, p.id, "refund-key-0001")
    refund = session.scalar(select(PaymentRefund))
    assert refund.status is RefundStatus.UNKNOWN
    session.expire_all()
    assert session.get(ResolutionCase, case.id).status is CaseStatus.ACTION_REQUIRED
    r = res.reconcile_refund(who("alice"), refund.id)
    assert r.status is RefundStatus.COMPLETED
    assert res.payments.last_reconcile_method == "replay_same_request_id"
    assert gateway.request_ids["refund_capture"] == ["og-ref-refund-key-0001"] * 2
    session.expire_all()
    assert session.get(ResolutionCase, case.id).status is CaseStatus.RESOLVED


def test_lost_response_holds_balance_and_reconciles_read_first_without_a_second_post(
    res, who, world, session, gateway
):
    agreement, auth, cap, run = world
    case = open_refund_case(res, who, agreement)
    p = agree_refund(res, who, case, "200.00")
    gateway.fail_after["refund_capture"] = PayPalUncertainError("response lost")
    with pytest.raises(PaymentUncertain):
        res.execute_refund(who("alice"), case.id, p.id, "refund-key-0001")
    refund = session.scalar(select(PaymentRefund))
    session.refresh(cap)
    assert refund.status is RefundStatus.UNKNOWN
    assert res.payments.refundable_balance(cap).minor == 30000  # still held
    # No new refund may consume money held by the uncertain one.
    pol = res.refund_policy(who("alice"), case.id, None)
    assert "REFUND_OUTCOME_UNKNOWN" in [x["code"] for x in pol.reasons]
    r = res.reconcile_refund(who("maya"), refund.id)
    assert r.status is RefundStatus.COMPLETED and r.paypal_refund_id
    assert res.payments.last_reconcile_method == "read_existing_refund"
    assert gateway.calls["refund_capture"] == 1
    order = gateway.get_order(auth.order.paypal_order_id)
    assert len(order["purchase_units"][0]["payments"]["refunds"]) == 1


def test_old_unknown_refund_needs_an_operator_and_keeps_the_balance_held(
    res, who, world, session, gateway
):
    agreement, auth, cap, run = world
    case = open_refund_case(res, who, agreement)
    p = agree_refund(res, who, case)
    gateway.fail_before["refund_capture"] = PayPalUncertainError("timeout")
    with pytest.raises(PaymentUncertain):
        res.execute_refund(who("alice"), case.id, p.id, "refund-key-0001")
    refund = session.scalar(select(PaymentRefund))
    session.execute(
        text("UPDATE payment_refunds SET created_at = now() - interval '7 hours' WHERE id = :i"),
        {"i": refund.id},
    )
    session.commit()
    r = res.reconcile_refund(who("alice"), refund.id)
    assert r.status is RefundStatus.RECONCILIATION_REQUIRED
    assert gateway.calls["refund_capture"] == 1  # no replay
    session.refresh(cap)
    assert res.payments.refundable_balance(cap).minor == 40000


def test_settlement_figures_are_paypal_reported(res, who, world, session, gateway):
    case = open_refund_case(res, who, world[0])
    p = agree_refund(res, who, case)
    gateway.override["refund_capture"] = lambda r: {
        **r,
        "seller_payable_breakdown": {
            "gross_amount": {"currency_code": "USD", "value": "100.00"},
            "paypal_fee": {"currency_code": "USD", "value": "3.29"},
            "net_amount": {"currency_code": "USD", "value": "96.71"},
        },
    }
    r = res.execute_refund(who("alice"), case.id, p.id, "refund-key-0001")
    assert (r.paypal_fee_returned_minor, r.merchant_debit_minor) == (329, 9671)
    del gateway.override["refund_capture"]  # PayPal reports no breakdown this time
    case2 = open_refund_case(res, who, world[0], "10.00")
    p2 = agree_refund(res, who, case2, "10.00")
    r2 = res.execute_refund(who("alice"), case2.id, p2.id, "refund-key-0002")
    assert (r2.paypal_fee_returned_minor, r2.merchant_debit_minor) == (None, None)  # not invented


# =================================================================== voids


@pytest.fixture
def uncaptured(session, svc, gateway, ids, who):
    agreement = locked_agreement_with_manual(session, ids, who)
    auth = authorized_payment(session, svc, gateway, agreement)
    return agreement, auth


def test_buyer_voids_an_uncaptured_authorization_idempotently(
    res, who, uncaptured, session, gateway, gov
):
    agreement, auth = uncaptured
    op = res.void(who("bob"), auth.id, reason="Project cancelled before delivery")
    assert op.status is VoidStatus.COMPLETED
    session.expire_all()
    assert session.get(PaymentAuthorization, auth.id).status is AuthorizationStatus.VOIDED
    again = res.void(who("bob"), auth.id, reason="again")
    assert again.id == op.id and gateway.calls["void_authorization"] == 1
    # A voided authorization can never be captured.
    p = gov.preview(who("bob"), auth.id)
    assert "AUTH_NOT_CAPTURABLE" in [r["code"] for r in p.reasons]
    assert session.scalar(select(AuditEvent).where(AuditEvent.action == "payment.void.completed"))


def test_void_permissions(res, who, uncaptured):
    agreement, auth = uncaptured
    for name in ("maya", "rita"):
        with pytest.raises(GovernanceError) as ei:
            res.void(who(name), auth.id, reason="not allowed to cancel")
        assert ei.value.code == "POLICY_NOT_SATISFIED" and "ACTOR_NOT_AUTHORIZED" in codes(ei)
    with pytest.raises(GovernanceError) as ei:
        res.void(who("eve"), auth.id, reason="outsider")
    assert ei.value.http_status == 404


def test_captured_authorization_cannot_be_voided(res, who, world):
    with pytest.raises(GovernanceError) as ei:
        res.void(who("bob"), world[1].id, reason="too late, use a refund")
    assert "ALREADY_CAPTURED" in codes(ei)


def test_unknown_capture_blocks_void(res, who, uncaptured, session, svc, gateway, gov, tmp_path):
    agreement, auth = uncaptured
    run = completed_run(session, who, agreement, tmp_path, form_ok=True)
    gateway.fail_after["capture_authorization"] = PayPalUncertainError("lost")
    with pytest.raises(PaymentUncertain):
        _review_and_capture(gov, who, agreement, auth, run)
    with pytest.raises(GovernanceError) as ei:
        res.void(who("bob"), auth.id, reason="cancel while unknown")
    assert {"CAPTURE_IN_PROGRESS"} <= set(codes(ei))
    assert gateway.calls["void_authorization"] == 0


def test_verified_delivery_needs_provider_consent_to_void(
    res, who, uncaptured, session, gov, tmp_path
):
    agreement, auth = uncaptured
    run = completed_run(session, who, agreement, tmp_path, form_ok=True)
    gov.record_review(
        who("rita"),
        run_id=run.id,
        criterion_id=manual_criterion_id(session, agreement),
        decision=ReviewDecision.SATISFIED,
        rationale="Checked keyboard navigation and contrast.",
        evidence_snapshot_sha256_seen=evidence_snapshot_sha256(session, run),
    )
    with pytest.raises(GovernanceError) as ei:
        res.void(who("bob"), auth.id, reason="buyer changed their mind")
    assert "PROVIDER_CONSENT_REQUIRED" in codes(ei)
    case = res.open_case(
        who("alice"),
        agreement,
        reason_category=ReasonCategory.CANCEL_BEFORE_CAPTURE,
        description="We need to cancel the project before payment.",
        requested_remedy=Remedy.CANCEL_AUTHORIZATION,
        requested_amount=None,
        criterion_ids=[],
        artifact_ids=[],
    )
    p = res.submit_proposal(
        who("alice"),
        case.id,
        expected_revision=1,
        remedy=Remedy.CANCEL_AUTHORIZATION,
        amount=None,
        rationale="Cancel the authorization by mutual consent",
        evidence_refs=[],
        approve=True,
    )
    res.decide(
        who("maya"),
        case.id,
        p.id,
        choice=ApprovalChoice.APPROVE,
        content_sha256=p.content_sha256,
        note=None,
    )
    op = res.void(who("bob"), auth.id, reason="mutual cancellation")
    assert op.status is VoidStatus.COMPLETED and op.case_id == case.id
    session.expire_all()
    assert session.get(ResolutionCase, case.id).status is CaseStatus.RESOLVED


def test_void_timeout_is_unknown_blocks_capture_and_reconciles(
    res, who, uncaptured, session, gateway, gov, tmp_path
):
    agreement, auth = uncaptured
    gateway.fail_after["void_authorization"] = PayPalUncertainError("void response lost")
    with pytest.raises(PaymentUncertain):
        res.void(who("bob"), auth.id, reason="cancel, then network drops")
    session.expire_all()
    op = session.scalar(select(AuthorizationVoid))
    assert op.status is VoidStatus.UNKNOWN
    assert session.get(PaymentAuthorization, auth.id).status is AuthorizationStatus.VOID_IN_PROGRESS
    p = gov.preview(who("bob"), auth.id)  # capture is blocked meanwhile
    assert "AUTH_NOT_CAPTURABLE" in [r["code"] for r in p.reasons]
    op = res.reconcile_void(who("bob"), op.id)
    assert (
        op.status is VoidStatus.COMPLETED
        and res.payments.last_reconcile_method == "read_authorization"
    )
    assert gateway.calls["void_authorization"] == 1


def test_void_and_capture_race_cannot_both_win(
    make_session, who, uncaptured, session, gateway, settings, gov, tmp_path
):
    agreement, auth = uncaptured
    run = completed_run(session, who, agreement, tmp_path, form_ok=True)
    gov.record_review(
        who("rita"),
        run_id=run.id,
        criterion_id=manual_criterion_id(session, agreement),
        decision=ReviewDecision.SATISFIED,
        rationale="Checked keyboard navigation and contrast.",
        evidence_snapshot_sha256_seen=evidence_snapshot_sha256(session, run),
    )
    case = Resolution(session=session, payments=gov.payments).open_case(
        who("alice"),
        agreement,
        reason_category=ReasonCategory.CANCEL_BEFORE_CAPTURE,
        description="Cancel before capture by consent.",
        requested_remedy=Remedy.CANCEL_AUTHORIZATION,
        requested_amount=None,
        criterion_ids=[],
        artifact_ids=[],
    )
    r0 = Resolution(session=session, payments=gov.payments)
    p = r0.submit_proposal(
        who("alice"),
        case.id,
        expected_revision=1,
        remedy=Remedy.CANCEL_AUTHORIZATION,
        amount=None,
        rationale="Cancel the authorization by consent",
        evidence_refs=[],
        approve=True,
    )
    r0.decide(
        who("maya"),
        case.id,
        p.id,
        choice=ApprovalChoice.APPROVE,
        content_sha256=p.content_sha256,
        note=None,
    )
    pv = gov.preview(who("bob"), auth.id)
    approval = gov.approve_capture(who("bob"), auth.id, pv.inputs_sha256)
    barrier = threading.Barrier(2)
    out: dict[str, str] = {}

    def capture():
        s = make_session()
        g = Governance(
            session=s, payments=PaymentService(session=s, gateway=gateway, settings=settings)
        )
        barrier.wait()
        try:
            g.capture(load_principal(s, who("bob").user_id), auth.id, approval.id)
            out["capture"] = "ok"
        except Exception as exc:
            out["capture"] = getattr(exc, "code", type(exc).__name__)
        finally:
            s.close()

    def void():
        s = make_session()
        r = Resolution(
            session=s, payments=PaymentService(session=s, gateway=gateway, settings=settings)
        )
        barrier.wait()
        try:
            r.void(load_principal(s, who("bob").user_id), auth.id, reason="race")
            out["void"] = "ok"
        except Exception as exc:
            out["void"] = getattr(exc, "code", type(exc).__name__)
        finally:
            s.close()

    threads = [threading.Thread(target=capture), threading.Thread(target=void)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(out.values()).count("ok") == 1, out
    assert gateway.calls["capture_authorization"] + gateway.calls["void_authorization"] == 1
    session.expire_all()
    a = session.get(PaymentAuthorization, auth.id)
    assert a.status in (AuthorizationStatus.CAPTURED, AuthorizationStatus.VOIDED)


# ============================================================ evidence / rework


def test_rework_evidence_is_a_new_run_and_the_original_snapshot_is_kept(
    res, who, world, session, tmp_path
):
    agreement, auth, cap, run = world
    case = open_refund_case(res, who, agreement)
    original = dict(session.get(ResolutionCase, case.id).evidence_snapshot)
    new_run = completed_run(session, who, agreement, tmp_path / "rework", form_ok=True)
    assert new_run.id != run.id
    res.link_evidence(who("maya"), case.id, new_run.id, "Fixed version re-verified")
    session.expire_all()
    c = session.get(ResolutionCase, case.id)
    assert (
        c.evidence_snapshot == original and c.evidence_snapshot["verification_run"]["id"] == run.id
    )
    linked = session.scalar(
        select(ResolutionEvent).where(ResolutionEvent.kind == "evidence_linked")
    )
    assert linked.data["verification_run_id"] == new_run.id
    with pytest.raises(GovernanceError):
        res.link_evidence(who("maya"), case.id, "not-a-run", None)


def test_closing_records_outcome_and_blocks_further_changes(res, who, world, session):
    case = open_refund_case(res, who, world[0])
    with pytest.raises(GovernanceError):  # provider cannot close the buyer's claim
        res.close(who("maya"), case.id, outcome="closed_no_action", reason="nope")
    res.close(who("alice"), case.id, outcome="closed_no_action", reason="Resolved informally")
    with pytest.raises(GovernanceError) as ei:
        res.post_message(who("alice"), case.id, "after close")
    assert ei.value.code == "CASE_CLOSED"
    _ = timedelta  # keep import for readability of time-window tests
