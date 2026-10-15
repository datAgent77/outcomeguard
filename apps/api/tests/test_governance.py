"""P06 governance with the scripted fake PayPal gateway (MOCKED payments — real Sandbox proof
is scripts/governance_demo.py). Verification results are persisted measurement dicts."""

import threading
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from outcomeguard.auth import Forbidden, load_principal
from outcomeguard.models import (
    CaptureApproval,
    CaptureStatus,
    ManualReviewDecision,
    OrganizationMembership,
    PaymentAuthorization,
    PaymentCapture,
    PolicyDecisionRecord,
    PolicyOutcome,
    ReviewDecision,
    Role,
    User,
    UserKind,
)
from outcomeguard.models.base import utcnow
from outcomeguard.paypal.gateway import PayPalUncertainError
from outcomeguard.services.governance import Governance, GovernanceError, evidence_snapshot_sha256
from outcomeguard.services.payments import PaymentRuleViolation, PaymentService
from tests.govhelpers import (
    authorized_payment,
    completed_run,
    locked_agreement_with_manual,
    manual_criterion_id,
)


@pytest.fixture
def gov(session, svc):
    return Governance(session=session, payments=svc)


@pytest.fixture
def world(session, svc, gateway, ids, who):
    agreement = locked_agreement_with_manual(session, ids, who)
    auth = authorized_payment(session, svc, gateway, agreement)
    return agreement, auth


def codes(exc_info):
    return [r["code"] for r in exc_info.value.reasons]


def review(gov, who, run, agreement, decision=ReviewDecision.SATISFIED, name="rita", snap=None):
    return gov.record_review(
        who(name),
        run_id=run.id,
        criterion_id=manual_criterion_id(gov.session, agreement),
        decision=decision,
        rationale="Checked keyboard navigation, contrast and labels (WCAG AA).",
        evidence_snapshot_sha256_seen=snap or evidence_snapshot_sha256(gov.session, run),
    )


def approve(gov, who, auth, name="bob"):
    p = gov.preview(who(name), auth.id)
    return gov.approve_capture(who(name), auth.id, p.inputs_sha256)


def ready(gov, who, world, tmp_path):
    agreement, auth = world
    run = completed_run(gov.session, who, agreement, tmp_path, form_ok=True)
    review(gov, who, run, agreement)
    return run


# ============================================================ verdict → policy


def test_broken_page_fail_blocks_capture(gov, who, world, tmp_path):
    agreement, auth = world
    completed_run(gov.session, who, agreement, tmp_path, form_ok=False)
    p = gov.preview(who("bob"), auth.id)
    assert p.decision is PolicyOutcome.BLOCKED
    assert {"BLOCKING_CRITERION_FAILED", "MANUAL_REVIEW_PENDING"} <= {r["code"] for r in p.reasons}
    with pytest.raises(GovernanceError) as ei:
        gov.approve_capture(who("bob"), auth.id, p.inputs_sha256)
    assert ei.value.code == "POLICY_NOT_SATISFIED" and "BLOCKING_CRITERION_FAILED" in codes(ei)


def test_fixed_page_needs_manual_review_blocks_capture(gov, who, world, tmp_path):
    agreement, auth = world
    completed_run(gov.session, who, agreement, tmp_path, form_ok=True)
    p = gov.preview(who("bob"), auth.id)
    assert p.decision is PolicyOutcome.NEEDS_REVIEW
    assert [r["code"] for r in p.reasons] == ["MANUAL_REVIEW_PENDING"]
    with pytest.raises(GovernanceError):
        gov.approve_capture(who("bob"), auth.id, p.inputs_sha256)


def test_human_review_then_approval_then_capture(gov, who, world, tmp_path, gateway):
    agreement, auth = world
    run = ready(gov, who, world, tmp_path)
    p = gov.preview(who("bob"), auth.id)
    assert p.decision is PolicyOutcome.ELIGIBLE_FOR_APPROVAL and p.reasons == []
    approval = gov.approve_capture(who("bob"), auth.id, p.inputs_sha256)
    cap = gov.capture(who("bob"), auth.id, approval.id)
    assert cap.status is CaptureStatus.COMPLETED
    assert (cap.policy_decision_id, cap.approval_id, cap.verification_run_id) != (None,) * 3
    assert cap.verification_run_id == run.id
    s = gov.session
    s.expire_all()
    assert s.get(CaptureApproval, approval.id).consumed_by_capture_id == cap.id
    purposes = [
        (r.purpose, r.decision.value)
        for r in s.scalars(select(PolicyDecisionRecord).order_by(PolicyDecisionRecord.evaluated_at))
    ]
    assert ("capture", "capture_approved") in purposes and ("settlement", "captured") in purposes
    assert gateway.calls["capture_authorization"] == 1


# ================================================================ separation


def test_provider_cannot_review_or_approve_own_delivery(gov, who, world, tmp_path):
    agreement, auth = world
    run = completed_run(gov.session, who, agreement, tmp_path, form_ok=True)
    with pytest.raises(Forbidden, match="provider"):
        review(gov, who, run, agreement, name="maya")
    review(gov, who, run, agreement)
    with pytest.raises(Forbidden, match="provider"):
        approve(gov, who, auth, name="maya")


def test_service_accounts_ai_cannot_review_or_approve(session, gov, who, world, tmp_path, ids):
    agreement, auth = world
    run = completed_run(session, who, agreement, tmp_path, form_ok=True)
    bot = User(email="ai-assistant@system.local", display_name="AI", kind=UserKind.SERVICE)
    session.add(bot)
    session.flush()
    for role in (Role.BUYER_REVIEWER, Role.BUYER_APPROVER):
        session.add(
            OrganizationMembership(organization_id=ids.buyer_org_id, user_id=bot.id, role=role)
        )
    session.commit()
    ai = load_principal(session, bot.id)
    with pytest.raises(Forbidden, match="human"):
        gov.record_review(
            ai,
            run_id=run.id,
            criterion_id=manual_criterion_id(session, agreement),
            decision=ReviewDecision.SATISFIED,
            rationale="AI says it is accessible.",
            evidence_snapshot_sha256_seen=evidence_snapshot_sha256(session, run),
        )
    review(gov, who, run, agreement)
    p = gov.preview(who("bob"), auth.id)
    with pytest.raises(Forbidden, match="human"):
        gov.approve_capture(ai, auth.id, p.inputs_sha256)


def test_ai_analysis_never_counts_as_review(session, gov, who, world, tmp_path, settings):
    from outcomeguard.ai.evidence import EvidenceIntelligence
    from outcomeguard.ai.gateway import AIGateway
    from outcomeguard.ai.provider import ScriptedProvider
    from tests.test_ai import analysis_json, explanation

    agreement, auth = world
    run = completed_run(session, who, agreement, tmp_path, form_ok=True)
    items = [explanation(r, insufficient_evidence=True) for r in run.results]
    reply = analysis_json(run, items, summary="Accessibility approved by the AI reviewer.")
    gw = AIGateway(session=session, provider=ScriptedProvider([reply]), settings=settings)
    a = EvidenceIntelligence(session, gw).analyze(run.id, actor="ai")
    assert a.grounded["pending_human_review"][0]["decision"] is None
    assert session.scalar(select(func.count()).select_from(ManualReviewDecision)) == 0
    assert gov.preview(who("bob"), auth.id).decision is PolicyOutcome.NEEDS_REVIEW


def test_rejected_manual_review_blocks(gov, who, world, tmp_path):
    agreement, auth = world
    run = completed_run(gov.session, who, agreement, tmp_path, form_ok=True)
    review(gov, who, run, agreement, decision=ReviewDecision.NOT_SATISFIED)
    p = gov.preview(who("bob"), auth.id)
    assert p.decision is PolicyOutcome.BLOCKED and p.reasons[0]["code"] == "MANUAL_REVIEW_REJECTED"


def test_review_must_match_current_evidence_and_is_single(gov, who, world, tmp_path):
    agreement, auth = world
    run = completed_run(gov.session, who, agreement, tmp_path, form_ok=True)
    with pytest.raises(GovernanceError) as ei:
        review(gov, who, run, agreement, snap="0" * 64)
    assert ei.value.code == "STALE_EVIDENCE_VIEW"
    review(gov, who, run, agreement)
    with pytest.raises(GovernanceError) as ei:
        review(gov, who, run, agreement, name="bob")
    assert ei.value.code == "ALREADY_DECIDED"


def test_cross_org_and_wrong_roles(gov, who, world, tmp_path):
    agreement, auth = world
    run = ready(gov, who, world, tmp_path)
    with pytest.raises(GovernanceError) as ei:
        gov.preview(who("eve"), auth.id)
    assert ei.value.http_status == 404
    with pytest.raises(Forbidden):  # reviewer may not approve captures
        approve(gov, who, auth, name="rita")
    with pytest.raises((Forbidden, GovernanceError)):
        review(gov, who, run, agreement, name="eve")


# ============================================================ approval binding


def test_approval_binds_to_seen_inputs(gov, who, world, tmp_path):
    agreement, auth = world
    ready(gov, who, world, tmp_path)
    with pytest.raises(GovernanceError) as ei:
        gov.approve_capture(who("bob"), auth.id, "f" * 64)
    assert ei.value.code == "POLICY_INPUTS_CHANGED"


def test_stale_evidence_after_approval_blocks_capture(gov, who, world, tmp_path):
    from outcomeguard.verification.service import submit_evidence

    agreement, auth = world
    ready(gov, who, world, tmp_path)
    approval = approve(gov, who, auth)
    submit_evidence(gov.session, who("maya"), agreement, delivery_url="https://landing.example/v2")
    with pytest.raises(GovernanceError) as ei:
        gov.capture(who("bob"), auth.id, approval.id)
    assert "STALE_EVIDENCE" in codes(ei)


def test_superseded_agreement_blocks_capture(gov, who, world, tmp_path):
    from outcomeguard.services.agreements import AgreementService

    agreement, auth = world
    ready(gov, who, world, tmp_path)
    approval = approve(gov, who, auth)
    svc = AgreementService(session=gov.session)
    v2 = svc.new_version(who("alice"), agreement.id)
    p = svc.propose(who("alice"), agreement.id, 2, expected_revision=v2.revision)
    svc.approve(who("bob"), agreement.id, 2, content_hash_seen=p.content_hash)
    with pytest.raises(GovernanceError) as ei:
        gov.capture(who("bob"), auth.id, approval.id)
    assert "AGREEMENT_SUPERSEDED" in codes(ei)


def test_expired_authorization_rejected(gov, who, world, tmp_path, session):
    agreement, auth = world
    ready(gov, who, world, tmp_path)
    approval = approve(gov, who, auth)
    a = session.get(PaymentAuthorization, auth.id)
    a.expires_at = utcnow() + timedelta(minutes=30)  # inside the 1 h safety margin
    session.commit()
    with pytest.raises(GovernanceError) as ei:
        gov.capture(who("bob"), auth.id, approval.id)
    assert "AUTH_EXPIRED" in codes(ei)
    p = gov.preview(who("bob"), auth.id)
    assert p.decision is PolicyOutcome.BLOCKED


def test_revoked_approver_cannot_be_used(gov, who, world, tmp_path, session, ids):
    agreement, auth = world
    ready(gov, who, world, tmp_path)
    approval = approve(gov, who, auth)
    session.execute(
        text("DELETE FROM organization_memberships WHERE user_id = :u"),
        {"u": ids.users["bob@acme.demo"]},
    )
    session.commit()
    with pytest.raises(GovernanceError) as ei:
        gov.capture(who("alice"), auth.id, approval.id)
    assert ei.value.code == "APPROVER_NO_LONGER_AUTHORIZED"


# ================================================================ duplicates


def test_duplicate_capture_is_blocked(gov, who, world, tmp_path, gateway):
    agreement, auth = world
    ready(gov, who, world, tmp_path)
    approval = approve(gov, who, auth)
    gov.capture(who("bob"), auth.id, approval.id)
    with pytest.raises((GovernanceError, PaymentRuleViolation)) as ei:
        gov.capture(who("bob"), auth.id, approval.id)
    assert getattr(ei.value, "code", "") in {"ALREADY_CAPTURED", "APPROVAL_ALREADY_USED"}
    with pytest.raises(GovernanceError) as ei:
        approve(gov, who, auth)
    assert "ALREADY_CAPTURED" in codes(ei)
    assert gateway.calls["capture_authorization"] == 1


def test_concurrent_captures_and_approvals(
    make_session, gov, who, world, tmp_path, gateway, settings, ids
):
    agreement, auth = world
    ready(gov, who, world, tmp_path)
    inputs = gov.preview(who("bob"), auth.id).inputs_sha256
    barrier = threading.Barrier(2)
    approvals: list[str] = []

    def approver(email):
        s = make_session()
        g = Governance(
            session=s, payments=PaymentService(session=s, gateway=gateway, settings=settings)
        )
        barrier.wait()
        approvals.append(g.approve_capture(load_principal(s, ids.users[email]), auth.id, inputs).id)
        s.close()

    ts = [
        threading.Thread(target=approver, args=(e,)) for e in ("bob@acme.demo", "alice@acme.demo")
    ]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(set(approvals)) == 1  # one approval for one set of facts
    barrier3 = threading.Barrier(3)
    outcomes: list[str] = []

    def capturer():
        s = make_session()
        g = Governance(
            session=s, payments=PaymentService(session=s, gateway=gateway, settings=settings)
        )
        barrier3.wait()
        try:
            g.capture(load_principal(s, ids.users["bob@acme.demo"]), auth.id, approvals[0])
            outcomes.append("captured")
        except (GovernanceError, PaymentRuleViolation) as exc:
            outcomes.append(exc.code)
        s.close()

    ts = [threading.Thread(target=capturer) for _ in range(3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert outcomes.count("captured") == 1, outcomes
    assert gateway.calls["capture_authorization"] == 1


def test_lost_paypal_response_is_unknown_then_reconciled(gov, who, world, tmp_path, gateway):
    agreement, auth = world
    ready(gov, who, world, tmp_path)
    approval = approve(gov, who, auth)
    gateway.fail_after["capture_authorization"] = PayPalUncertainError("read timeout")
    from outcomeguard.services.payments import PaymentUncertain

    with pytest.raises(PaymentUncertain) as ei:
        gov.capture(who("bob"), auth.id, approval.id)
    cap_id = ei.value.entity_id
    assert gov.session.get(PaymentCapture, cap_id).status is CaptureStatus.UNKNOWN
    with pytest.raises(GovernanceError) as e2:  # no new attempt while uncertain
        approve(gov, who, auth)
    assert "CAPTURE_IN_PROGRESS" in codes(e2)
    cap = gov.reconcile(who("bob"), cap_id)
    assert cap.status is CaptureStatus.COMPLETED
    ids_ = gateway.request_ids["capture_authorization"]
    assert len(ids_) == 2 and ids_[0] == ids_[1] and len(gateway._captures) == 1


# ======================================================= bypass and immutability


def test_capture_without_policy_is_refused_by_service(svc, world):
    agreement, auth = world
    with pytest.raises(PaymentRuleViolation) as ei:
        svc.capture_authorization(auth.id, actor="dev-endpoint")
    assert ei.value.code == "POLICY_REQUIRED"


def test_database_rejects_capture_rows_without_policy_refs(session, world):
    agreement, auth = world
    with pytest.raises(DBAPIError, match="requires policy decision"):
        session.execute(
            text(
                "INSERT INTO payment_captures (id, payment_authorization_id, request_id, amount_minor,"
                " currency, final_capture, status, requested_by, created_at, updated_at) VALUES"
                " ('x', :a, 'r-x', 50000, 'USD', true, 'requested', 'sql', now(), now())"
            ),
            {"a": auth.id},
        )
        session.flush()
    session.rollback()


def test_governance_records_are_immutable(gov, who, world, tmp_path, session):
    agreement, auth = world
    ready(gov, who, world, tmp_path)
    approval = approve(gov, who, auth)
    for sql in (
        "UPDATE manual_review_decisions SET decision = 'not_satisfied'",
        "DELETE FROM manual_review_decisions",
        "UPDATE policy_decisions SET decision = 'eligible_for_approval'",
        "DELETE FROM capture_approvals",
        "UPDATE capture_approvals SET inputs_sha256 = repeat('0', 64)",
    ):
        with pytest.raises(DBAPIError):
            session.execute(text(sql))
            session.flush()
        session.rollback()
    session.execute(
        text("UPDATE capture_approvals SET consumed_by_capture_id = 'c1' WHERE id = :i"),
        {"i": approval.id},
    )
    session.commit()
    with pytest.raises(DBAPIError, match="consumed once"):
        session.execute(
            text("UPDATE capture_approvals SET consumed_by_capture_id = 'c2' WHERE id = :i"),
            {"i": approval.id},
        )
        session.flush()
    session.rollback()


def test_manual_review_db_trigger_rejects_non_manual_criteria(session, who, world, tmp_path):
    agreement, auth = world
    run = completed_run(session, who, agreement, tmp_path, form_ok=True)
    form_crit = next(r.criterion_id for r in run.results if r.kind == "contact_form")
    with pytest.raises(DBAPIError, match="pending manual criterion"):
        session.add(
            ManualReviewDecision(
                agreement_version_id=run.agreement_version_id,
                agreement_content_hash=run.agreement_content_hash,
                criterion_id=form_crit,
                verification_run_id=run.id,
                evidence_snapshot_sha256="0" * 64,
                decision=ReviewDecision.SATISFIED,
                rationale="sneaky direct insert",
                reviewer_user_id=load_principal(session, who("rita").user_id).user_id,
                reviewer_role="buyer_reviewer",
                organization_id=agreement.buyer_organization_id,
            )
        )
        session.flush()
    session.rollback()


def test_production_refuses_dev_endpoints():
    from outcomeguard.config import Settings

    with pytest.raises(ValueError, match="ENABLE_SANDBOX_PROOF"):
        Settings(app_env="production", enable_sandbox_proof=True)
