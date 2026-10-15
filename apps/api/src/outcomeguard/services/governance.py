"""P06 payment governance: evidence-bound human reviews, fresh deterministic policy
evaluation, buyer capture approval, and the policy gate used inside capture reservation.

Three separate human acts, never merged:
  1. agreement approval (P03)   — buyer_approver locks the version;
  2. manual-review decision     — buyer_reviewer/approver decides a manual criterion,
                                  bound to run + criterion + evidence snapshot hash;
  3. capture approval           — buyer_approver/admin approves capturing THIS
                                  authorization for THIS policy-inputs hash.
At capture time everything is re-gathered and re-evaluated under the authorization row
lock; a historical decision or approval is never sufficient on its own.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from outcomeguard.auth import Forbidden, Principal
from outcomeguard.domain.policy import (
    CriterionIn,
    PolicyInputs,
    PolicyResult,
    ResultIn,
    ReviewIn,
    evaluate,
)
from outcomeguard.models import (
    AcceptanceCriterion,
    Agreement,
    AgreementVersion,
    CaptureApproval,
    CaptureStatus,
    EvidenceArtifact,
    EvidenceSubmission,
    ManualReviewDecision,
    MerchantAccount,
    OrganizationMembership,
    PaymentAuthorization,
    PaymentCapture,
    PolicyDecisionRecord,
    PolicyOutcome,
    ReviewDecision,
    Role,
    RunStatus,
    User,
    UserKind,
    VerificationRun,
)
from outcomeguard.models.audit import record_audit
from outcomeguard.services.payments import CaptureRefs, PaymentService

REVIEW_ROLES = (Role.BUYER_REVIEWER, Role.BUYER_APPROVER)
CAPTURE_ROLES = (Role.BUYER_APPROVER, Role.BUYER_ADMIN)


class GovernanceError(Exception):
    def __init__(
        self, code: str, message: str, *, http_status: int = 409, reasons: list[dict] | None = None
    ) -> None:
        self.code, self.message, self.http_status = code, message, http_status
        self.reasons = reasons or []
        super().__init__(f"{code}: {message}")


def evidence_snapshot_sha256(session: Session, run: VerificationRun) -> str:
    """Hash of exactly what a reviewer sees for a run: every result's outcome, reason and
    measurements, and every artifact's content hash."""
    arts = session.scalars(
        select(EvidenceArtifact.sha256).where(EvidenceArtifact.run_id == run.id)
    ).all()
    body = {
        "run_id": run.id,
        "agreement_version_id": run.agreement_version_id,
        "agreement_content_hash": run.agreement_content_hash,
        "results": sorted(
            [
                {
                    "criterion_id": r.criterion_id,
                    "outcome": r.outcome.value,
                    "reason_code": r.reason_code,
                    "measured_sha256": hashlib.sha256(
                        json.dumps(r.measured, sort_keys=True, default=str).encode()
                    ).hexdigest(),
                }
                for r in run.results
            ],
            key=lambda x: x["criterion_id"],
        ),
        "artifacts": sorted(arts),
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


@dataclass
class Governance:
    session: Session
    payments: PaymentService

    # ---------------------------------------------------------------- gathering

    def _merchant_org(self, merchant_account_id: str) -> str:
        m = self.session.get(MerchantAccount, merchant_account_id)
        assert m is not None
        return m.organization_id

    def latest_completed_run(self, version_id: str) -> VerificationRun | None:
        return self.session.scalar(
            select(VerificationRun)
            .where(
                VerificationRun.agreement_version_id == version_id,
                VerificationRun.status == RunStatus.COMPLETED,
            )
            .order_by(VerificationRun.created_at.desc())
            .limit(1)
        )

    def gather(self, auth: PaymentAuthorization) -> tuple[PolicyInputs, dict[str, Any]]:
        s = self.session
        order = auth.order
        version = (
            s.get(AgreementVersion, order.agreement_version_id)
            if order.agreement_version_id
            else None
        )
        agreement = s.get(Agreement, version.agreement_id) if version else None
        merchant = s.get(MerchantAccount, order.merchant_account_id)
        capture = s.scalar(
            select(PaymentCapture).where(PaymentCapture.payment_authorization_id == auth.id)
        )
        run = self.latest_completed_run(version.id) if version else None
        latest_sub = (
            s.scalar(
                select(EvidenceSubmission.id)
                .where(EvidenceSubmission.agreement_version_id == version.id)
                .order_by(EvidenceSubmission.created_at.desc())
                .limit(1)
            )
            if version
            else None
        )
        criteria = (
            list(
                s.scalars(
                    select(AcceptanceCriterion).where(
                        AcceptanceCriterion.agreement_version_id == version.id
                    )
                )
            )
            if version
            else []
        )
        snapshot = evidence_snapshot_sha256(s, run) if run else None
        reviews: list[ReviewIn] = []
        if run:
            merchant_org = self._merchant_org(order.merchant_account_id)
            for d in s.scalars(
                select(ManualReviewDecision).where(
                    ManualReviewDecision.verification_run_id == run.id
                )
            ):
                reviewer = s.get(User, d.reviewer_user_id)
                is_provider = (
                    s.scalar(
                        select(OrganizationMembership.id)
                        .where(
                            OrganizationMembership.user_id == d.reviewer_user_id,
                            OrganizationMembership.organization_id == merchant_org,
                        )
                        .limit(1)
                    )
                    is not None
                )
                reviews.append(
                    ReviewIn(
                        criterion_id=d.criterion_id,
                        verification_run_id=d.verification_run_id,
                        agreement_version_id=d.agreement_version_id,
                        agreement_content_hash=d.agreement_content_hash,
                        evidence_snapshot_sha256=d.evidence_snapshot_sha256,
                        decision=d.decision.value,
                        reviewer_is_human=bool(reviewer and reviewer.kind is UserKind.HUMAN),
                        reviewer_is_provider=is_provider,
                    )
                )
        inputs = PolicyInputs(
            agreement_id=agreement.id if agreement else "",
            agreement_current_locked_version_id=agreement.current_locked_version_id
            if agreement
            else None,
            version_id=version.id if version else None,
            version_status=version.status.value if version else None,
            version_content_hash=version.content_hash if version else None,
            version_amount_minor=version.amount_minor if version else None,
            version_currency=version.currency if version else None,
            agreement_merchant_account_id=agreement.merchant_account_id if agreement else "",
            order_agreement_version_id=order.agreement_version_id,
            order_amount_minor=order.amount_minor,
            order_currency=order.currency,
            order_merchant_account_id=order.merchant_account_id,
            merchant_paypal_id=merchant.paypal_merchant_id if merchant else None,
            authorization_id=auth.id,
            authorization_status=auth.status.value,
            authorization_amount_minor=auth.amount_minor,
            authorization_currency=auth.currency,
            authorization_payee=auth.payee_merchant_id,
            authorization_expires_at=auth.expires_at.astimezone(UTC).isoformat()
            if auth.expires_at
            else None,
            capture_status=capture.status.value if capture else None,
            latest_submission_id=latest_sub,
            run_id=run.id if run else None,
            run_submission_id=run.submission_id if run else None,
            run_status=run.status.value if run else None,
            run_verdict=run.verdict.value if run and run.verdict else None,
            run_version_id=run.agreement_version_id if run else None,
            run_content_hash=run.agreement_content_hash if run else None,
            evidence_snapshot_sha256=snapshot,
            criteria=tuple(CriterionIn(c.id, c.key, c.kind.value, c.blocking) for c in criteria),
            results=tuple(
                ResultIn(r.criterion_id, r.outcome.value) for r in (run.results if run else [])
            ),
            reviews=tuple(reviews),
        )
        return inputs, {"agreement": agreement, "version": version, "run": run}

    def _record(
        self,
        auth: PaymentAuthorization,
        inputs: PolicyInputs,
        result: PolicyResult,
        *,
        purpose: str,
        actor: str,
        outcome: PolicyOutcome | None = None,
    ) -> PolicyDecisionRecord:
        row = PolicyDecisionRecord(
            payment_authorization_id=auth.id,
            agreement_version_id=inputs.version_id,
            verification_run_id=inputs.run_id,
            purpose=purpose,
            decision=outcome or PolicyOutcome(result.decision.value),
            reasons=result.reasons_json(),
            inputs=inputs.canonical(),
            inputs_sha256=result.inputs_sha256,
            evaluated_by=actor[:200],
        )
        self.session.add(row)
        self.session.flush()
        record_audit(
            self.session,
            actor=actor,
            action=f"policy.{purpose}.{row.decision.value}",
            entity_type="policy_decision",
            entity_id=row.id,
            data={
                "authorization": auth.id,
                "reasons": [r["code"] for r in row.reasons],
                "inputs_sha256": row.inputs_sha256,
            },
        )
        return row

    def _reader_check(self, principal: Principal, inputs: PolicyInputs, agreement) -> None:
        if agreement is None:
            raise GovernanceError("NOT_FOUND", "authorization not found", http_status=404)
        buyer, merchant_org = (
            agreement.buyer_organization_id,
            self._merchant_org(agreement.merchant_account_id),
        )
        if not (principal.member_of(buyer) or principal.member_of(merchant_org)):
            raise GovernanceError("NOT_FOUND", "authorization not found", http_status=404)

    # ----------------------------------------------------------------- preview

    def preview(self, principal: Principal, auth_id: str) -> PolicyDecisionRecord:
        auth = self.session.get(PaymentAuthorization, auth_id)
        if auth is None:
            raise GovernanceError("NOT_FOUND", "authorization not found", http_status=404)
        inputs, ctx = self.gather(auth)
        self._reader_check(principal, inputs, ctx["agreement"])
        result = evaluate(inputs, datetime.now(UTC))
        row = self._record(auth, inputs, result, purpose="preview", actor=principal.label)
        self.session.commit()
        return row

    # ----------------------------------------------------------- manual review

    def record_review(
        self,
        principal: Principal,
        *,
        run_id: str,
        criterion_id: str,
        decision: ReviewDecision,
        rationale: str,
        evidence_snapshot_sha256_seen: str,
    ) -> ManualReviewDecision:
        principal.require_human("deciding a manual review")
        s = self.session
        run = s.get(VerificationRun, run_id, with_for_update=True)
        if run is None:
            raise GovernanceError("NOT_FOUND", "run not found", http_status=404)
        version = s.get(AgreementVersion, run.agreement_version_id)
        assert version is not None
        agreement = s.get(Agreement, version.agreement_id)
        assert agreement is not None
        merchant_org = self._merchant_org(agreement.merchant_account_id)
        if principal.member_of(merchant_org):
            raise Forbidden("the service provider cannot review its own delivery")
        principal.require(
            agreement.buyer_organization_id, *REVIEW_ROLES, action="deciding a manual review"
        )
        if run.status is not RunStatus.COMPLETED:
            raise GovernanceError("RUN_NOT_COMPLETED", f"run is {run.status.value}")
        if agreement.current_locked_version_id != version.id:
            raise GovernanceError("AGREEMENT_SUPERSEDED", "run is for a superseded version")
        result = next((r for r in run.results if r.criterion_id == criterion_id), None)
        if result is None or result.outcome.value != "manual_review_required":
            raise GovernanceError(
                "NOT_A_MANUAL_CRITERION",
                "criterion has no manual_review_required result in this run",
                http_status=422,
            )
        current = evidence_snapshot_sha256(s, run)
        if evidence_snapshot_sha256_seen != current:
            raise GovernanceError(
                "STALE_EVIDENCE_VIEW", "the evidence changed since you loaded it; reload and review"
            )
        if len(rationale.strip()) < 10:
            raise GovernanceError(
                "RATIONALE_REQUIRED", "explain the decision (≥10 characters)", http_status=422
            )
        role = next(r for r in REVIEW_ROLES if principal.has(agreement.buyer_organization_id, r))
        row = ManualReviewDecision(
            agreement_version_id=version.id,
            agreement_content_hash=version.content_hash or "",
            criterion_id=criterion_id,
            verification_run_id=run.id,
            evidence_snapshot_sha256=current,
            decision=decision,
            rationale=rationale.strip(),
            reviewer_user_id=principal.user_id,
            reviewer_role=role.value,
            organization_id=agreement.buyer_organization_id,
        )
        s.add(row)
        try:
            s.flush()
        except IntegrityError as exc:
            s.rollback()
            raise GovernanceError(
                "ALREADY_DECIDED", "this criterion already has a decision for this run"
            ) from exc
        record_audit(
            s,
            actor=principal.label,
            action=f"manual_review.{decision.value}",
            entity_type="manual_review_decision",
            entity_id=row.id,
            data={
                "run_id": run.id,
                "criterion_id": criterion_id,
                "evidence_snapshot_sha256": current,
            },
        )
        s.commit()
        return row

    # --------------------------------------------------------- capture approval

    def _require_capture_role(
        self, principal: Principal, agreement: Agreement, action: str
    ) -> Role:
        principal.require_human(action)
        if principal.member_of(self._merchant_org(agreement.merchant_account_id)):
            raise Forbidden("the service provider cannot approve or capture its own payment")
        principal.require(agreement.buyer_organization_id, *CAPTURE_ROLES, action=action)
        return next(r for r in CAPTURE_ROLES if principal.has(agreement.buyer_organization_id, r))

    def approve_capture(
        self, principal: Principal, auth_id: str, inputs_sha256_seen: str
    ) -> CaptureApproval:
        s = self.session
        auth = s.scalar(
            select(PaymentAuthorization).where(PaymentAuthorization.id == auth_id).with_for_update()
        )
        if auth is None:
            raise GovernanceError("NOT_FOUND", "authorization not found", http_status=404)
        inputs, ctx = self.gather(auth)
        self._reader_check(principal, inputs, ctx["agreement"])
        role = self._require_capture_role(principal, ctx["agreement"], "approving a capture")
        result = evaluate(inputs, datetime.now(UTC))
        decision = self._record(auth, inputs, result, purpose="approval", actor=principal.label)
        if not result.eligible:
            s.commit()  # keep the evidence of the refused attempt
            raise GovernanceError(
                "POLICY_NOT_SATISFIED",
                f"policy is {result.decision.value}",
                reasons=result.reasons_json(),
            )
        if inputs_sha256_seen != result.inputs_sha256:
            s.commit()
            raise GovernanceError(
                "POLICY_INPUTS_CHANGED",
                "the facts changed since you reviewed them; reload the policy",
            )
        existing = s.scalar(
            select(CaptureApproval).where(
                CaptureApproval.payment_authorization_id == auth.id,
                CaptureApproval.inputs_sha256 == result.inputs_sha256,
            )
        )
        if existing:
            s.commit()
            return existing  # concurrent/duplicate approval of the same facts → same record
        approval = CaptureApproval(
            payment_authorization_id=auth.id,
            policy_decision_id=decision.id,
            inputs_sha256=result.inputs_sha256,
            approver_user_id=principal.user_id,
            approver_role=role.value,
            organization_id=ctx["agreement"].buyer_organization_id,
        )
        s.add(approval)
        s.flush()
        record_audit(
            s,
            actor=principal.label,
            action="capture.approved",
            entity_type="capture_approval",
            entity_id=approval.id,
            data={"authorization": auth.id, "inputs_sha256": result.inputs_sha256},
        )
        s.commit()
        return approval

    # ----------------------------------------------------------------- capture

    def capture(self, principal: Principal, auth_id: str, approval_id: str) -> PaymentCapture:
        s = self.session
        auth0 = s.get(PaymentAuthorization, auth_id)
        if auth0 is None:
            raise GovernanceError("NOT_FOUND", "authorization not found", http_status=404)
        inputs0, ctx0 = self.gather(auth0)
        self._reader_check(principal, inputs0, ctx0["agreement"])
        self._require_capture_role(principal, ctx0["agreement"], "capturing a payment")
        actor = principal.label

        def gate(auth: PaymentAuthorization) -> CaptureRefs:
            # Runs inside the reservation transaction with the authorization row locked.
            approval = s.scalar(
                select(CaptureApproval).where(CaptureApproval.id == approval_id).with_for_update()
            )
            if approval is None or approval.payment_authorization_id != auth.id:
                raise GovernanceError(
                    "APPROVAL_NOT_FOR_THIS_PAYMENT", "approval not found", http_status=404
                )
            if approval.consumed_by_capture_id:
                raise GovernanceError("APPROVAL_ALREADY_USED", "approval was already used")
            approver = s.get(User, approval.approver_user_id)
            still = s.scalar(
                select(OrganizationMembership.id)
                .where(
                    OrganizationMembership.user_id == approval.approver_user_id,
                    OrganizationMembership.organization_id == approval.organization_id,
                    OrganizationMembership.role.in_(CAPTURE_ROLES),
                )
                .limit(1)
            )
            if approver is None or approver.kind is not UserKind.HUMAN or still is None:
                raise GovernanceError(
                    "APPROVER_NO_LONGER_AUTHORIZED", "the approver no longer holds a capture role"
                )
            inputs, _ = self.gather(auth)  # fresh facts, under the lock
            result = evaluate(inputs, datetime.now(UTC))
            if not result.eligible:
                self._record(auth, inputs, result, purpose="capture", actor=actor)
                s.commit()
                raise GovernanceError(
                    "POLICY_NOT_SATISFIED",
                    f"policy is {result.decision.value}",
                    reasons=result.reasons_json(),
                )
            if result.inputs_sha256 != approval.inputs_sha256:
                self._record(auth, inputs, result, purpose="capture", actor=actor)
                s.commit()
                raise GovernanceError(
                    "APPROVAL_STALE", "facts changed after the approval; approve again"
                )
            decision = self._record(
                auth,
                inputs,
                result,
                purpose="capture",
                actor=actor,
                outcome=PolicyOutcome.CAPTURE_APPROVED,
            )

            def on_reserved(capture: PaymentCapture) -> None:
                approval.consumed_by_capture_id = capture.id

            assert inputs.run_id is not None
            return CaptureRefs(
                policy_decision_id=decision.id,
                approval_id=approval.id,
                verification_run_id=inputs.run_id,
                on_reserved=on_reserved,
            )

        capture = self.payments.capture_authorization(auth_id, actor=actor, gate=gate)
        self._mark_captured(capture, actor)
        return capture

    def reconcile(self, principal: Principal, capture_id: str) -> PaymentCapture:
        cap = self.session.get(PaymentCapture, capture_id)
        if cap is None:
            raise GovernanceError("NOT_FOUND", "capture not found", http_status=404)
        inputs, ctx = self.gather(cap.authorization)
        self._reader_check(principal, inputs, ctx["agreement"])
        self._require_capture_role(principal, ctx["agreement"], "reconciling a capture")
        cap = self.payments.reconcile_capture(capture_id)
        self._mark_captured(cap, principal.label)
        return cap

    def _mark_captured(self, capture: PaymentCapture, actor: str) -> None:
        if capture.status is not CaptureStatus.COMPLETED or capture.policy_decision_id is None:
            return
        done = self.session.scalar(
            select(PolicyDecisionRecord.id)
            .where(
                PolicyDecisionRecord.payment_authorization_id == capture.payment_authorization_id,
                PolicyDecisionRecord.decision == PolicyOutcome.CAPTURED,
            )
            .limit(1)
        )
        if done:
            return
        approved = self.session.get(PolicyDecisionRecord, capture.policy_decision_id)
        assert approved is not None
        row = PolicyDecisionRecord(
            payment_authorization_id=capture.payment_authorization_id,
            agreement_version_id=approved.agreement_version_id,
            verification_run_id=approved.verification_run_id,
            purpose="settlement",
            decision=PolicyOutcome.CAPTURED,
            reasons=[
                {"code": "PAYPAL_CAPTURE_COMPLETED", "detail": capture.paypal_capture_id or ""}
            ],
            inputs=approved.inputs,
            inputs_sha256=approved.inputs_sha256,
            evaluated_by=actor,
        )
        self.session.add(row)
        self.session.commit()
