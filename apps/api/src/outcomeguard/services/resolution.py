"""P08 resolution workflow: cases, negotiation, and the governed money operations
(refund of a completed capture, void of an uncaptured authorization).

Rules (docs/RESOLUTION_AND_REFUNDS.md):
* opening a case never moves money;
* a refund needs an accepted proposal approved by BOTH sides (human accounts, each for
  its own organization), bound to the proposal's content hash, plus a fresh `refund`
  policy decision evaluated under the capture row lock;
* a void is governed by the `void` policy under the authorization row lock; once P06
  says the delivery is verified, the buyer alone cannot void — an accepted, two-sided
  `cancel_authorization` proposal is required;
* AI and service accounts can do none of this.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from outcomeguard.auth import Forbidden, Principal
from outcomeguard.domain.policy import Decision as P06Decision
from outcomeguard.domain.policy import evaluate as evaluate_capture_policy
from outcomeguard.domain.resolution_policy import (
    ApprovalIn,
    FinDecision,
    FinResult,
    RefundIn,
    RefundInputs,
    TransitionError,
    VoidInputs,
    evaluate_refund,
    evaluate_void,
    next_state,
)
from outcomeguard.models import (
    AcceptanceCriterion,
    Agreement,
    AgreementVersion,
    ApprovalChoice,
    AuthorizationVoid,
    CaptureStatus,
    CaseStatus,
    EvidenceArtifact,
    FinancialDecision,
    FinancialPolicyDecision,
    FinancialPurpose,
    ManualReviewDecision,
    MerchantAccount,
    PaymentAuthorization,
    PaymentCapture,
    PaymentOrder,
    PaymentRefund,
    ProposalStatus,
    ReasonCategory,
    RefundStatus,
    Remedy,
    ResolutionApproval,
    ResolutionCase,
    ResolutionEvent,
    ResolutionProposal,
    Role,
    RunStatus,
    Side,
    User,
    UserKind,
    VerificationRun,
    VoidStatus,
)
from outcomeguard.models.audit import record_audit
from outcomeguard.models.resolution import MONETARY_REMEDIES, REFUND_REMEDIES
from outcomeguard.money import Money
from outcomeguard.services.governance import Governance, GovernanceError, evidence_snapshot_sha256
from outcomeguard.services.payments import (
    PaymentService,
    PaymentUncertain,
    RefundRefs,
    VoidRefs,
)

BUYER_FINANCE = (Role.BUYER_ADMIN, Role.BUYER_APPROVER)
BUYER_ANY = (Role.BUYER_ADMIN, Role.BUYER_APPROVER, Role.BUYER_REVIEWER)
MERCHANT_FINANCE = (Role.MERCHANT_ADMIN, Role.MERCHANT_APPROVER)
MERCHANT_ANY = (Role.MERCHANT_ADMIN, Role.MERCHANT_APPROVER, Role.PROVIDER)
REFUND_EXECUTORS_MERCHANT = (Role.MERCHANT_ADMIN,)
SCRIPTED_MARKER = "SCRIPTED DEMO DECISION"


def _sha(obj: object) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def _fail(code: str, message: str, status: int = 409, reasons: list[dict] | None = None):
    return GovernanceError(code, message, http_status=status, reasons=reasons)


@dataclass
class CaseView:
    case: ResolutionCase
    side: Side | None  # the caller's side (None for read-only callers)


@dataclass
class Resolution:
    session: Session
    payments: PaymentService

    # ------------------------------------------------------------ access

    def _merchant_org(self, agreement: Agreement) -> str:
        m = self.session.get(MerchantAccount, agreement.merchant_account_id)
        assert m is not None
        return m.organization_id

    def _side(self, principal: Principal, case: ResolutionCase) -> Side | None:
        if principal.member_of(case.buyer_organization_id):
            return Side.BUYER
        if principal.member_of(case.merchant_organization_id):
            return Side.PROVIDER
        return None

    def _case(self, principal: Principal, case_id: str, *, lock: bool = False) -> CaseView:
        stmt = select(ResolutionCase).where(ResolutionCase.id == case_id)
        if lock:
            stmt = stmt.with_for_update()
        case = self.session.scalar(stmt)
        if case is None:
            raise _fail("NOT_FOUND", "case not found", 404)
        side = self._side(principal, case)
        if side is None:
            raise _fail("NOT_FOUND", "case not found", 404)  # no existence leak
        return CaseView(case, side)

    def _require(self, principal: Principal, case: ResolutionCase, side: Side, roles, action: str):
        principal.require_human(action)
        org = case.buyer_organization_id if side is Side.BUYER else case.merchant_organization_id
        principal.require(org, *roles, action=action)

    def _event(
        self,
        case: ResolutionCase,
        kind: str,
        *,
        principal: Principal | None,
        side: Side | str | None,
        body: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> ResolutionEvent:
        ev = ResolutionEvent(
            case_id=case.id,
            kind=kind,
            author_user_id=principal.user_id if principal else None,
            side=(side.value if isinstance(side, Side) else side) or "system",
            body=body,
            data=data or {},
        )
        self.session.add(ev)
        self.session.flush()
        record_audit(
            self.session,
            actor=principal.label if principal else "system",
            action=f"resolution.{kind}",
            entity_type="resolution_case",
            entity_id=case.id,
            data={"event": ev.id, **(data or {})},
        )
        return ev

    def _move(self, case: ResolutionCase, event: str) -> None:
        try:
            new = next_state(case.status.value, event)
        except TransitionError as exc:
            raise _fail("INVALID_TRANSITION", str(exc)) from exc
        if new != case.status.value:
            old = case.status.value
            case.status = CaseStatus(new)
            if new in ("resolved", "closed_no_action", "rejected", "cancelled"):
                case.closed_at = datetime.now(UTC)
            self.session.add(
                ResolutionEvent(
                    case_id=case.id,
                    kind="status_changed",
                    side="system",
                    data={"from": old, "to": new, "event": event},
                )
            )
        case.revision += 1

    # ------------------------------------------------------------ payment refs

    def _payment_for(
        self, agreement: Agreement
    ) -> tuple[PaymentAuthorization | None, PaymentCapture | None]:
        version_ids = [v.id for v in agreement.versions]
        order = self.session.scalar(
            select(PaymentOrder)
            .where(PaymentOrder.agreement_version_id.in_(version_ids))
            .where(PaymentOrder.authorization.has())
            .order_by(PaymentOrder.created_at.desc())
            .limit(1)
        )
        auth = order.authorization if order else None
        cap = auth.capture if auth else None
        return auth, cap

    # ------------------------------------------------------------ open

    def open_case(
        self,
        principal: Principal,
        agreement: Agreement,
        *,
        reason_category: ReasonCategory,
        description: str,
        requested_remedy: Remedy,
        requested_amount: Money | None,
        criterion_ids: list[str],
        artifact_ids: list[str],
    ) -> ResolutionCase:
        principal.require_human("opening a resolution case")
        # The caller can already read this agreement (provider side): an honest 403.
        if not principal.has(agreement.buyer_organization_id, *BUYER_FINANCE):
            raise Forbidden("opening a resolution case requires buyer_admin or buyer_approver")
        if agreement.current_locked_version_id is None:
            raise _fail("AGREEMENT_NOT_LOCKED", "only locked agreements can be disputed")
        version = self.session.get(AgreementVersion, agreement.current_locked_version_id)
        assert version is not None
        auth, cap = self._payment_for(agreement)
        captured = cap is not None and cap.status is CaptureStatus.COMPLETED
        if requested_remedy in REFUND_REMEDIES and not captured:
            raise _fail("CAPTURE_NOT_COMPLETED", "a refund needs a completed capture", 422)
        if requested_remedy is Remedy.CANCEL_AUTHORIZATION and (auth is None or cap is not None):
            raise _fail(
                "AUTHORIZATION_NOT_VOIDABLE",
                "cancellation applies to an authorized, uncaptured payment",
                422,
            )
        if requested_amount is not None:
            if requested_amount.currency != version.currency:
                raise _fail("CURRENCY_MISMATCH", "currency differs from the agreement", 422)
            if requested_amount.minor <= 0 or (cap and requested_amount.minor > cap.amount_minor):
                raise _fail("REFUND_AMOUNT_INVALID", "amount must be within the capture", 422)

        snapshot = self._snapshot(version, auth, cap, criterion_ids, artifact_ids)
        case = ResolutionCase(
            agreement_id=agreement.id,
            agreement_version_id=version.id,
            agreement_content_hash=version.content_hash or "",
            payment_authorization_id=auth.id if auth else None,
            payment_capture_id=cap.id if captured and cap else None,
            buyer_organization_id=agreement.buyer_organization_id,
            merchant_organization_id=self._merchant_org(agreement),
            opened_by_user_id=principal.user_id,
            reason_category=reason_category,
            description=description.strip(),
            requested_remedy=requested_remedy,
            requested_amount_minor=requested_amount.minor if requested_amount else None,
            currency=version.currency,
            evidence_snapshot=snapshot,
            evidence_snapshot_sha256=_sha(snapshot),
            status=CaseStatus.OPEN,
            revision=1,
        )
        self.session.add(case)
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            raise _fail("CASE_ALREADY_OPEN", "this payment already has an open case") from exc
        self._event(
            case,
            "opened",
            principal=principal,
            side=Side.BUYER,
            body=case.description,
            data={
                "reason": reason_category.value,
                "requested_remedy": requested_remedy.value,
                "requested_amount": str(requested_amount) if requested_amount else None,
                "evidence_snapshot_sha256": case.evidence_snapshot_sha256,
            },
        )
        self.session.commit()
        return case

    def _snapshot(
        self,
        version: AgreementVersion,
        auth: PaymentAuthorization | None,
        cap: PaymentCapture | None,
        criterion_ids: list[str],
        artifact_ids: list[str],
    ) -> dict[str, Any]:
        s = self.session
        limitations: list[str] = []
        crits = {c.id: c for c in version.criteria}
        bad = [c for c in criterion_ids if c not in crits]
        if bad:
            raise _fail("EVIDENCE_REFERENCE_INVALID", f"criteria not in this version: {bad}", 422)
        run = s.scalar(
            select(VerificationRun)
            .where(
                VerificationRun.agreement_version_id == version.id,
                VerificationRun.status == RunStatus.COMPLETED,
            )
            .order_by(VerificationRun.created_at.desc())
            .limit(1)
        )
        arts: list[EvidenceArtifact] = []
        reviews: list[dict[str, Any]] = []
        if run is None:
            limitations.append("no completed verification run existed when the case was opened")
            if artifact_ids:
                raise _fail("EVIDENCE_REFERENCE_INVALID", "no run to reference artifacts from", 422)
        else:
            all_arts = {
                a.id: a
                for a in s.scalars(
                    select(EvidenceArtifact).where(EvidenceArtifact.run_id == run.id)
                )
            }
            missing = [a for a in artifact_ids if a not in all_arts]
            if missing:
                raise _fail(
                    "EVIDENCE_REFERENCE_INVALID", f"artifacts not in run {run.id}: {missing}", 422
                )
            arts = [all_arts[a] for a in artifact_ids] if artifact_ids else list(all_arts.values())
            for d in s.scalars(
                select(ManualReviewDecision).where(
                    ManualReviewDecision.verification_run_id == run.id
                )
            ):
                reviews.append(
                    {
                        "id": d.id,
                        "criterion_id": d.criterion_id,
                        "decision": d.decision.value,
                        "evidence_snapshot_sha256": d.evidence_snapshot_sha256,
                        "scripted": d.rationale.startswith(SCRIPTED_MARKER),
                    }
                )
        if cap is None:
            limitations.append("no capture: no money has been transferred to the provider")
        return {
            "agreement_version_id": version.id,
            "agreement_content_hash": version.content_hash,
            "criteria": [
                {"id": c, "key": crits[c].key, "title": crits[c].title} for c in criterion_ids
            ],
            "verification_run": (
                {
                    "id": run.id,
                    "verdict": run.verdict.value if run.verdict else None,
                    "evidence_snapshot_sha256": evidence_snapshot_sha256(s, run),
                    "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                    "results": [
                        {
                            "criterion_id": r.criterion_id,
                            "key": r.criterion_key,
                            "outcome": r.outcome.value,
                        }
                        for r in run.results
                    ],
                }
                if run
                else None
            ),
            "artifacts": [
                {"id": a.id, "kind": a.kind.value, "label": a.label, "sha256": a.sha256}
                for a in arts
            ],
            "manual_reviews": reviews,
            "payment": {
                "paypal_order_id": auth.order.paypal_order_id if auth else None,
                "paypal_authorization_id": auth.paypal_authorization_id if auth else None,
                "authorization_status": auth.status.value if auth else None,
                "paypal_capture_id": cap.paypal_capture_id if cap else None,
                "capture_status": cap.status.value if cap else None,
                "amount": Money(cap.amount_minor, cap.currency).to_decimal_str() if cap else None,
                "currency": cap.currency if cap else version.currency,
                "environment": auth.order.environment.value if auth else None,
            },
            "limitations": limitations,
        }

    # ------------------------------------------------------------ read

    def list_for_agreement(
        self, principal: Principal, agreement: Agreement
    ) -> list[ResolutionCase]:
        rows = self.session.scalars(
            select(ResolutionCase)
            .where(ResolutionCase.agreement_id == agreement.id)
            .order_by(ResolutionCase.created_at.desc())
        ).all()
        return [c for c in rows if self._side(principal, c) is not None]

    def get(self, principal: Principal, case_id: str) -> CaseView:
        return self._case(principal, case_id)

    # ------------------------------------------------------------ messages

    def post_message(self, principal: Principal, case_id: str, body: str) -> ResolutionEvent:
        view = self._case(principal, case_id, lock=True)
        case, side = view.case, view.side
        assert side is not None
        roles = BUYER_ANY if side is Side.BUYER else MERCHANT_ANY
        self._require(principal, case, side, roles, "writing in a resolution case")
        self._open_or_fail(case)
        ev = self._event(case, "message", principal=principal, side=side, body=body.strip())
        if side is Side.PROVIDER and case.status is CaseStatus.OPEN:
            self._move(case, "counterparty_activity")
        else:
            case.revision += 1
        self.session.commit()
        return ev

    def link_evidence(
        self, principal: Principal, case_id: str, run_id: str, note: str | None
    ) -> ResolutionEvent:
        view = self._case(principal, case_id, lock=True)
        case, side = view.case, view.side
        assert side is not None
        roles = BUYER_ANY if side is Side.BUYER else MERCHANT_ANY
        self._require(principal, case, side, roles, "adding evidence to a resolution case")
        self._open_or_fail(case)
        run = self.session.get(VerificationRun, run_id)
        version = self.session.get(AgreementVersion, run.agreement_version_id) if run else None
        if run is None or version is None or version.agreement_id != case.agreement_id:
            raise _fail("EVIDENCE_REFERENCE_INVALID", "run does not belong to this agreement", 422)
        data = {
            "verification_run_id": run.id,
            "run_status": run.status.value,
            "verdict": run.verdict.value if run.verdict else None,
            "evidence_snapshot_sha256": evidence_snapshot_sha256(self.session, run)
            if run.status is RunStatus.COMPLETED
            else None,
        }
        ev = self._event(
            case, "evidence_linked", principal=principal, side=side, body=note, data=data
        )
        if side is Side.PROVIDER and case.status is CaseStatus.OPEN:
            self._move(case, "counterparty_activity")
        else:
            case.revision += 1
        self.session.commit()
        return ev

    @staticmethod
    def _open_or_fail(case: ResolutionCase) -> None:
        if case.status.value in ("resolved", "closed_no_action", "rejected", "cancelled"):
            raise _fail("CASE_CLOSED", f"case is {case.status.value}")

    # ------------------------------------------------------------ proposals

    def _remaining_minor(self, cap: PaymentCapture) -> int:
        return self.payments.refundable_balance(cap).minor

    def submit_proposal(
        self,
        principal: Principal,
        case_id: str,
        *,
        expected_revision: int,
        remedy: Remedy,
        amount: Money | None,
        rationale: str,
        evidence_refs: list[dict[str, Any]],
        approve: bool,
    ) -> ResolutionProposal:
        view = self._case(principal, case_id, lock=True)
        case, side = view.case, view.side
        assert side is not None
        monetary = remedy in MONETARY_REMEDIES
        roles: tuple[Role, ...]
        if side is Side.BUYER:
            roles = BUYER_FINANCE
        else:
            roles = MERCHANT_FINANCE if monetary else MERCHANT_ANY
        self._require(principal, case, side, roles, "proposing a resolution")
        self._open_or_fail(case)
        if case.status in (CaseStatus.EXECUTING,):
            raise _fail("REFUND_IN_PROGRESS", "a money operation for this case is in progress")
        if case.revision != expected_revision:
            raise _fail("CASE_CHANGED", "the case changed since you loaded it; reload and retry")
        cap = (
            self.session.get(PaymentCapture, case.payment_capture_id)
            if case.payment_capture_id
            else None
        )
        if remedy in REFUND_REMEDIES:
            if cap is None or cap.status is not CaptureStatus.COMPLETED:
                raise _fail("CAPTURE_NOT_COMPLETED", "a refund needs a completed capture", 422)
            remaining = self._remaining_minor(cap)
            if remedy is Remedy.FULL_REFUND:
                if amount is not None and amount.minor != remaining:
                    raise _fail(
                        "REFUND_AMOUNT_INVALID", "a full refund is the remaining balance", 422
                    )
                amount = Money(remaining, cap.currency)
            if amount is None or amount.minor <= 0:
                raise _fail("REFUND_AMOUNT_INVALID", "amount required", 422)
            if amount.currency != cap.currency:
                raise _fail("CURRENCY_MISMATCH", "currency differs from the capture", 422)
            if amount.minor > remaining:
                raise _fail(
                    "REFUND_EXCEEDS_REMAINING",
                    f"remaining is {Money(remaining, cap.currency)}",
                    422,
                )
        elif remedy is Remedy.CANCEL_AUTHORIZATION:
            auth = (
                self.session.get(PaymentAuthorization, case.payment_authorization_id)
                if case.payment_authorization_id
                else None
            )
            if auth is None or cap is not None or auth.capture is not None:
                raise _fail("AUTHORIZATION_NOT_VOIDABLE", "no uncaptured authorization", 422)
            amount = None
        else:
            amount = None
        open_ones = self.session.scalars(
            select(ResolutionProposal).where(
                ResolutionProposal.case_id == case.id,
                ResolutionProposal.status.in_([ProposalStatus.OPEN, ProposalStatus.ACCEPTED]),
            )
        ).all()
        for p in open_ones:
            p.status = ProposalStatus.SUPERSEDED
            p.decided_at = datetime.now(UTC)
        number = (
            self.session.scalar(
                select(func.max(ResolutionProposal.number)).where(
                    ResolutionProposal.case_id == case.id
                )
            )
            or 0
        ) + 1
        content = {
            "case_id": case.id,
            "number": number,
            "side": side.value,
            "remedy": remedy.value,
            "amount_minor": amount.minor if amount else None,
            "currency": case.currency,
            "rationale": rationale.strip(),
            "evidence_refs": evidence_refs,
            "case_revision": case.revision,
            "capture_id": case.payment_capture_id,
            "agreement_content_hash": case.agreement_content_hash,
        }
        proposal = ResolutionProposal(
            case_id=case.id,
            number=number,
            author_user_id=principal.user_id,
            side=side,
            remedy=remedy,
            amount_minor=content["amount_minor"],
            currency=case.currency,
            rationale=content["rationale"],
            evidence_refs=evidence_refs,
            content_sha256=_sha(content),
            case_revision=case.revision,
            status=ProposalStatus.OPEN,
        )
        self.session.add(proposal)
        self.session.flush()
        self._event(
            case,
            "proposal_submitted",
            principal=principal,
            side=side,
            body=proposal.rationale,
            data={
                "proposal_id": proposal.id,
                "number": number,
                "remedy": remedy.value,
                "amount": str(amount) if amount else None,
                "content_sha256": proposal.content_sha256,
                "superseded": [p.id for p in open_ones],
            },
        )
        self._move(case, "proposal_submitted")
        if approve:
            self._record_decision(principal, case, proposal, side, ApprovalChoice.APPROVE, None)
        self.session.commit()
        return proposal

    def decide(
        self,
        principal: Principal,
        case_id: str,
        proposal_id: str,
        *,
        choice: ApprovalChoice,
        content_sha256: str,
        note: str | None,
    ) -> ResolutionProposal:
        view = self._case(principal, case_id, lock=True)
        case, side = view.case, view.side
        assert side is not None
        proposal = self.session.scalar(
            select(ResolutionProposal)
            .where(ResolutionProposal.id == proposal_id, ResolutionProposal.case_id == case.id)
            .with_for_update()
        )
        if proposal is None:
            raise _fail("NOT_FOUND", "proposal not found", 404)
        monetary = proposal.remedy in MONETARY_REMEDIES
        roles = (
            BUYER_FINANCE
            if side is Side.BUYER
            else (MERCHANT_FINANCE if monetary else MERCHANT_ANY)
        )
        self._require(principal, case, side, roles, "deciding a resolution proposal")
        self._open_or_fail(case)
        if proposal.status is not ProposalStatus.OPEN or proposal.content_sha256 != content_sha256:
            raise _fail("APPROVAL_STALE", f"proposal is {proposal.status.value} or changed; reload")
        self._record_decision(principal, case, proposal, side, choice, note)
        self.session.commit()
        return proposal

    def _record_decision(
        self,
        principal: Principal,
        case: ResolutionCase,
        proposal: ResolutionProposal,
        side: Side,
        choice: ApprovalChoice,
        note: str | None,
    ) -> None:
        org = case.buyer_organization_id if side is Side.BUYER else case.merchant_organization_id
        role = next((r for o, r in principal.roles if o == org), None)
        self.session.add(
            ResolutionApproval(
                proposal_id=proposal.id,
                case_id=case.id,
                side=side,
                choice=choice,
                approver_user_id=principal.user_id,
                approver_role=role.value if role else "",
                organization_id=org,
                proposal_content_sha256=proposal.content_sha256,
                note=note,
            )
        )
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            raise _fail(
                "ALREADY_DECIDED", f"the {side.value} side already decided this proposal"
            ) from exc
        self._event(
            case,
            f"proposal_{'approved' if choice is ApprovalChoice.APPROVE else 'rejected'}",
            principal=principal,
            side=side,
            body=note,
            data={"proposal_id": proposal.id, "content_sha256": proposal.content_sha256},
        )
        now = datetime.now(UTC)
        if choice is ApprovalChoice.REJECT:
            proposal.status = ProposalStatus.REJECTED
            proposal.decided_at = now
            self._move(case, "proposal_rejected")
            return
        sides = set(
            self.session.scalars(
                select(ResolutionApproval.side).where(
                    ResolutionApproval.proposal_id == proposal.id,
                    ResolutionApproval.choice == ApprovalChoice.APPROVE,
                )
            )
        )
        if sides >= {Side.BUYER, Side.PROVIDER}:
            proposal.status = ProposalStatus.ACCEPTED
            proposal.decided_at = now
            self._move(case, "proposal_accepted")
        else:
            case.revision += 1

    def withdraw_proposal(
        self, principal: Principal, case_id: str, proposal_id: str
    ) -> ResolutionProposal:
        view = self._case(principal, case_id, lock=True)
        case = view.case
        proposal = self.session.get(ResolutionProposal, proposal_id)
        if proposal is None or proposal.case_id != case.id:
            raise _fail("NOT_FOUND", "proposal not found", 404)
        principal.require_human("withdrawing a proposal")
        if proposal.author_user_id != principal.user_id:
            raise _fail("FORBIDDEN", "only the author can withdraw a proposal", 403)
        if proposal.status is not ProposalStatus.OPEN:
            raise _fail("APPROVAL_STALE", f"proposal is {proposal.status.value}")
        proposal.status = ProposalStatus.WITHDRAWN
        proposal.decided_at = datetime.now(UTC)
        self._event(
            case,
            "proposal_withdrawn",
            principal=principal,
            side=view.side,
            data={"proposal_id": proposal.id},
        )
        self._move(case, "proposal_withdrawn")
        self.session.commit()
        return proposal

    # ------------------------------------------------------------ closing

    def close(
        self, principal: Principal, case_id: str, *, outcome: str, reason: str
    ) -> ResolutionCase:
        view = self._case(principal, case_id, lock=True)
        case, side = view.case, view.side
        assert side is not None
        event = {
            "resolved": "closed_resolved",
            "closed_no_action": "closed_no_action",
            "cancelled": "withdrawn_by_buyer",
            "rejected": "claim_rejected",
        }.get(outcome)
        if event is None:
            raise _fail("INVALID_OUTCOME", "unknown outcome", 422)
        if outcome == "resolved":
            accepted = self.session.scalar(
                select(ResolutionProposal).where(
                    ResolutionProposal.case_id == case.id,
                    ResolutionProposal.status == ProposalStatus.ACCEPTED,
                )
            )
            if accepted is None or accepted.remedy in MONETARY_REMEDIES:
                raise _fail(
                    "RESOLUTION_NOT_AGREED",
                    "only an agreed non-monetary remedy is closed manually; money operations "
                    "resolve the case when PayPal confirms them",
                )
            roles = BUYER_FINANCE if side is Side.BUYER else MERCHANT_FINANCE
            self._require(principal, case, side, roles, "closing a resolution case")
            accepted.status = ProposalStatus.EXECUTED
            accepted.decided_at = datetime.now(UTC)
        else:
            if side is not Side.BUYER:
                raise _fail("FORBIDDEN", "only the buyer can withdraw or close a claim", 403)
            self._require(principal, case, side, BUYER_FINANCE, "closing a resolution case")
            for p in self.session.scalars(
                select(ResolutionProposal).where(
                    ResolutionProposal.case_id == case.id,
                    ResolutionProposal.status.in_([ProposalStatus.OPEN, ProposalStatus.ACCEPTED]),
                )
            ):
                p.status = ProposalStatus.SUPERSEDED
                p.decided_at = datetime.now(UTC)
        self._event(
            case, "closed", principal=principal, side=side, body=reason, data={"outcome": outcome}
        )
        case.outcome = outcome
        case.closure_reason = reason
        self._move(case, event)
        self.session.commit()
        return case

    # ------------------------------------------------------------ refund policy

    def _refund_inputs(
        self,
        principal: Principal,
        case: ResolutionCase,
        proposal: ResolutionProposal | None,
        cap: PaymentCapture | None,
    ) -> RefundInputs:
        s = self.session
        human = principal.is_human
        actor_ok = human and (
            principal.has(case.buyer_organization_id, *BUYER_FINANCE)
            or principal.has(case.merchant_organization_id, *REFUND_EXECUTORS_MERCHANT)
        )
        approvals: list[ApprovalIn] = []
        if proposal is not None:
            for a in s.scalars(
                select(ResolutionApproval).where(ResolutionApproval.proposal_id == proposal.id)
            ):
                user = s.get(User, a.approver_user_id)
                expected_org = (
                    case.buyer_organization_id
                    if a.side is Side.BUYER
                    else case.merchant_organization_id
                )
                approvals.append(
                    ApprovalIn(
                        side=a.side.value,
                        choice=a.choice.value,
                        content_sha256=a.proposal_content_sha256,
                        human=bool(user and user.kind is UserKind.HUMAN),
                        organization_matches=a.organization_id == expected_org,
                    )
                )
        cap_agreement = None
        if cap is not None:
            order = cap.authorization.order
            v = (
                s.get(AgreementVersion, order.agreement_version_id)
                if order.agreement_version_id
                else None
            )
            cap_agreement = v.agreement_id if v else None
        return RefundInputs(
            actor_authorized=actor_ok,
            case_id=case.id,
            case_status=case.status.value,
            case_agreement_id=case.agreement_id,
            case_capture_id=case.payment_capture_id,
            proposal_id=proposal.id if proposal else None,
            proposal_case_id=proposal.case_id if proposal else None,
            proposal_status=proposal.status.value if proposal else None,
            proposal_remedy=proposal.remedy.value if proposal else None,
            proposal_amount_minor=proposal.amount_minor if proposal else None,
            proposal_currency=proposal.currency if proposal else None,
            proposal_content_sha256=proposal.content_sha256 if proposal else None,
            approvals=tuple(sorted(approvals, key=lambda a: (a.side, a.choice))),
            capture_id=cap.id if cap else None,
            capture_status=cap.status.value if cap else None,
            capture_agreement_id=cap_agreement,
            capture_amount_minor=cap.amount_minor if cap else 0,
            capture_currency=cap.currency if cap else None,
            refunds=tuple(
                sorted(
                    (
                        RefundIn(r.status.value, r.amount_minor, r.currency, r.proposal_id)
                        for r in (cap.refunds if cap else [])
                    ),
                    key=lambda x: (x.status, x.amount_minor, x.proposal_id or ""),
                )
            ),
        )

    def _persist(
        self,
        purpose: FinancialPurpose,
        result: FinResult,
        inputs: Any,
        principal: Principal,
        *,
        case_id: str | None = None,
        proposal_id: str | None = None,
        auth_id: str | None = None,
        capture_id: str | None = None,
        preview: bool = False,
    ) -> FinancialPolicyDecision:
        row = FinancialPolicyDecision(
            purpose=purpose,
            decision=FinancialDecision(result.decision.value),
            reasons=[{"code": r.code, "detail": r.detail} for r in result.reasons],
            inputs={**asdict(inputs), "figures": result.figures},
            inputs_sha256=result.inputs_sha256,
            case_id=case_id,
            proposal_id=proposal_id,
            payment_authorization_id=auth_id,
            payment_capture_id=capture_id,
            evaluated_by=principal.label,
        )
        self.session.add(row)
        self.session.flush()
        record_audit(
            self.session,
            actor=principal.label,
            # Previews (a screen asking "may we?") are persisted but kept out of the default
            # ledger view, like P06 previews; evaluations that gate money are not previews.
            action=f"policy.preview.{purpose.value}_{result.decision.value}"
            if preview
            else f"policy.{purpose.value}.{result.decision.value}",
            entity_type="financial_policy_decision",
            entity_id=row.id,
            data={
                "reasons": [r.code for r in result.reasons],
                "case": case_id,
                "proposal": proposal_id,
            },
        )
        return row

    def _target_proposal(
        self, case: ResolutionCase, proposal_id: str | None
    ) -> ResolutionProposal | None:
        if proposal_id:
            p = self.session.get(ResolutionProposal, proposal_id)
            return p if p and p.case_id == case.id else None
        return self.session.scalar(
            select(ResolutionProposal)
            .where(ResolutionProposal.case_id == case.id)
            .order_by(ResolutionProposal.number.desc())
            .limit(1)
        )

    def refund_policy(
        self, principal: Principal, case_id: str, proposal_id: str | None
    ) -> FinancialPolicyDecision:
        view = self._case(principal, case_id)
        case = view.case
        proposal = self._target_proposal(case, proposal_id)
        cap = (
            self.session.get(PaymentCapture, case.payment_capture_id)
            if case.payment_capture_id
            else None
        )
        inputs = self._refund_inputs(principal, case, proposal, cap)
        row = self._persist(
            FinancialPurpose.REFUND,
            evaluate_refund(inputs),
            inputs,
            principal,
            case_id=case.id,
            proposal_id=proposal.id if proposal else None,
            capture_id=cap.id if cap else None,
            preview=True,
        )
        self.session.commit()
        return row

    # ------------------------------------------------------------ refund execution

    def execute_refund(
        self, principal: Principal, case_id: str, proposal_id: str, idempotency_key: str
    ) -> PaymentRefund:
        principal.require_human("executing a refund")
        view = self._case(principal, case_id)
        case = view.case
        prior = self.session.scalar(
            select(PaymentRefund).where(PaymentRefund.request_id == f"og-ref-{idempotency_key}")
        )
        if prior is not None:  # same request replayed (double click, retry): original result
            if prior.proposal_id != proposal_id:
                raise _fail("IDEMPOTENCY_KEY_REUSED", "key was used for another refund", 422)
            return prior
        proposal = self.session.get(ResolutionProposal, proposal_id)
        if proposal is None or proposal.case_id != case.id:
            raise _fail("NOT_FOUND", "proposal not found", 404)
        if case.payment_capture_id is None or proposal.amount_minor is None:
            raise _fail("CAPTURE_NOT_COMPLETED", "this case has no captured payment to refund", 422)
        amount = Money(proposal.amount_minor, proposal.currency)
        case_pk, proposal_pk = case.id, proposal.id

        def gate(capture: PaymentCapture, requested: Money) -> RefundRefs:
            # Capture row is locked by the payment layer; lock the case and re-read all.
            locked = self.session.scalar(
                select(ResolutionCase).where(ResolutionCase.id == case_pk).with_for_update()
            )
            prop = self.session.scalar(
                select(ResolutionProposal)
                .where(ResolutionProposal.id == proposal_pk)
                .with_for_update()
            )
            assert locked is not None and prop is not None
            inputs = self._refund_inputs(principal, locked, prop, capture)
            result = evaluate_refund(inputs)
            if requested.minor != (prop.amount_minor or 0) or requested.currency != prop.currency:
                result = FinResult(
                    FinDecision.BLOCKED, result.reasons, result.inputs_sha256, result.figures
                )
            row = self._persist(
                FinancialPurpose.REFUND,
                result,
                inputs,
                principal,
                case_id=locked.id,
                proposal_id=prop.id,
                capture_id=capture.id,
            )
            if result.decision is not FinDecision.ALLOWED:
                self.session.commit()  # keep the blocked decision as evidence
                raise _fail(
                    "POLICY_NOT_SATISFIED",
                    "the refund policy does not allow this refund",
                    reasons=[{"code": r.code, "detail": r.detail} for r in result.reasons],
                )

            def on_reserved(refund: PaymentRefund) -> None:
                self._move(locked, "execution_reserved")
                self._event(
                    locked,
                    "refund_requested",
                    principal=principal,
                    side=self._side(principal, locked),
                    data={
                        "refund_id": refund.id,
                        "proposal_id": prop.id,
                        "amount": str(requested),
                        "policy_decision_id": row.id,
                        "request_id": refund.request_id,
                    },
                )

            return RefundRefs(
                resolution_case_id=locked.id,
                proposal_id=prop.id,
                policy_decision_id=row.id,
                on_reserved=on_reserved,
            )

        try:
            refund = self.payments.refund_capture(
                case.payment_capture_id,
                amount=amount,
                idempotency_key=idempotency_key,
                actor=principal.label,
                note=f"OutcomeGuard resolution {case.id[:8]}",
                gate=gate,
            )
        except PaymentUncertain as exc:
            self.settle_case_after_refund(exc.entity_id)
            raise
        except Exception:
            # PayPal rejected (4xx): the refund row is FAILED; reflect it on the case.
            failed = self.session.scalar(
                select(PaymentRefund).where(PaymentRefund.request_id == f"og-ref-{idempotency_key}")
            )
            if failed is not None:
                self.settle_case_after_refund(failed.id)
            raise
        self.settle_case_after_refund(refund.id)
        return refund

    def settle_case_after_refund(self, refund_id: str) -> None:
        """Move the case according to what is now known about its refund."""
        s = self.session
        s.expire_all()
        refund = s.get(PaymentRefund, refund_id)
        if refund is None or refund.resolution_case_id is None:
            return
        case = s.scalar(
            select(ResolutionCase)
            .where(ResolutionCase.id == refund.resolution_case_id)
            .with_for_update()
        )
        assert case is not None
        st = refund.status
        if st is RefundStatus.COMPLETED and case.status in (
            CaseStatus.EXECUTING,
            CaseStatus.ACTION_REQUIRED,
        ):
            prop = s.get(ResolutionProposal, refund.proposal_id)
            if prop is not None and prop.status is ProposalStatus.ACCEPTED:
                prop.status = ProposalStatus.EXECUTED
                prop.decided_at = datetime.now(UTC)
            self._event(
                case,
                "refund_completed",
                principal=None,
                side="system",
                data={
                    "refund_id": refund.id,
                    "paypal_refund_id": refund.paypal_refund_id,
                    "amount": str(Money(refund.amount_minor, refund.currency)),
                },
            )
            case.outcome = f"refunded {Money(refund.amount_minor, refund.currency)}"
            self._move(case, "execution_completed")
        elif st in (
            RefundStatus.UNKNOWN,
            RefundStatus.RECONCILIATION_REQUIRED,
            RefundStatus.FAILED,
        ) and (case.status is CaseStatus.EXECUTING):
            self._event(
                case,
                f"refund_{st.value}",
                principal=None,
                side="system",
                data={"refund_id": refund.id},
            )
            self._move(
                case, "execution_uncertain" if st is not RefundStatus.FAILED else "execution_failed"
            )
        elif (
            st in (RefundStatus.UNKNOWN, RefundStatus.RECONCILIATION_REQUIRED)
            and case.status is CaseStatus.ACTION_REQUIRED
        ):
            self._event(
                case,
                f"refund_{st.value}",
                principal=None,
                side="system",
                data={"refund_id": refund.id},
            )
        s.commit()

    def reconcile_refund(self, principal: Principal, refund_id: str) -> PaymentRefund:
        refund = self.session.get(PaymentRefund, refund_id)
        if refund is None or refund.resolution_case_id is None:
            raise _fail("NOT_FOUND", "refund not found", 404)
        view = self._case(principal, refund.resolution_case_id)
        case = view.case
        principal.require_human("reconciling a refund")
        if not (
            principal.has(case.buyer_organization_id, *BUYER_FINANCE)
            or principal.has(case.merchant_organization_id, *REFUND_EXECUTORS_MERCHANT)
        ):
            raise _fail("FORBIDDEN", "reconciling requires a finance role", 403)
        refund = self.payments.reconcile_refund(refund_id)
        self.settle_case_after_refund(refund.id)
        return refund

    # ------------------------------------------------------------ void

    def void_inputs(
        self, principal: Principal, auth: PaymentAuthorization
    ) -> tuple[VoidInputs, ResolutionCase | None]:
        s = self.session
        order = auth.order
        version = (
            s.get(AgreementVersion, order.agreement_version_id)
            if order.agreement_version_id
            else None
        )
        agreement = s.get(Agreement, version.agreement_id) if version else None
        if agreement is None:
            raise _fail("NOT_FOUND", "authorization not found", 404)
        if not (
            principal.member_of(agreement.buyer_organization_id)
            or principal.member_of(self._merchant_org(agreement))
        ):
            raise _fail("NOT_FOUND", "authorization not found", 404)
        actor_ok = principal.is_human and principal.has(
            agreement.buyer_organization_id, *BUYER_FINANCE
        )
        capture = s.scalar(
            select(PaymentCapture).where(PaymentCapture.payment_authorization_id == auth.id)
        )
        op = s.scalar(
            select(AuthorizationVoid).where(AuthorizationVoid.payment_authorization_id == auth.id)
        )
        p06_inputs, _ = Governance(session=s, payments=self.payments).gather(auth)
        verified = (
            evaluate_capture_policy(p06_inputs, datetime.now(UTC)).decision
            is P06Decision.ELIGIBLE_FOR_APPROVAL
        )
        case = s.scalar(
            select(ResolutionCase)
            .where(ResolutionCase.payment_authorization_id == auth.id)
            .where(
                ResolutionCase.status.in_(
                    [CaseStatus.AGREED, CaseStatus.ACTION_REQUIRED, CaseStatus.EXECUTING]
                )
            )
            .order_by(ResolutionCase.created_at.desc())
            .limit(1)
        )
        consent = None
        if case is not None:
            prop = s.scalar(
                select(ResolutionProposal).where(
                    ResolutionProposal.case_id == case.id,
                    ResolutionProposal.status == ProposalStatus.ACCEPTED,
                    ResolutionProposal.remedy == Remedy.CANCEL_AUTHORIZATION,
                )
            )
            consent = prop.id if prop else None
        return (
            VoidInputs(
                actor_authorized=actor_ok,
                authorization_id=auth.id,
                agreement_bound=True,
                authorization_status=auth.status.value,
                capture_status=capture.status.value if capture else None,
                void_status=op.status.value if op else None,
                delivery_verified=verified,
                agreed_cancel_proposal_id=consent,
            ),
            case if consent else None,
        )

    def void_policy(self, principal: Principal, auth_id: str) -> FinancialPolicyDecision:
        auth = self.session.get(PaymentAuthorization, auth_id)
        if auth is None:
            raise _fail("NOT_FOUND", "authorization not found", 404)
        inputs, _ = self.void_inputs(principal, auth)
        row = self._persist(
            FinancialPurpose.VOID,
            evaluate_void(inputs),
            inputs,
            principal,
            auth_id=auth.id,
            preview=True,
        )
        self.session.commit()
        return row

    def void(self, principal: Principal, auth_id: str, *, reason: str) -> AuthorizationVoid:
        principal.require_human("cancelling an authorization")
        auth = self.session.get(PaymentAuthorization, auth_id)
        if auth is None:
            raise _fail("NOT_FOUND", "authorization not found", 404)
        self.void_inputs(principal, auth)  # 404 for outsiders before any PayPal call
        existing = self.session.scalar(
            select(AuthorizationVoid).where(AuthorizationVoid.payment_authorization_id == auth.id)
        )
        if existing is not None and existing.status is VoidStatus.COMPLETED:
            return existing  # repeated request

        def gate(locked: PaymentAuthorization) -> VoidRefs:
            inputs, case = self.void_inputs(principal, locked)
            result = evaluate_void(inputs)
            row = self._persist(FinancialPurpose.VOID, result, inputs, principal, auth_id=locked.id)
            if result.decision is not FinDecision.ALLOWED:
                self.session.commit()
                raise _fail(
                    "POLICY_NOT_SATISFIED",
                    "the cancellation policy does not allow this void",
                    reasons=[{"code": r.code, "detail": r.detail} for r in result.reasons],
                )
            if case is not None:
                self._move(case, "execution_reserved")
                self._event(
                    case,
                    "void_requested",
                    principal=principal,
                    side=Side.BUYER,
                    data={"policy_decision_id": row.id},
                )
            return VoidRefs(
                policy_decision_id=row.id,
                case_id=case.id if case else None,
                proposal_id=inputs.agreed_cancel_proposal_id,
            )

        try:
            op = self.payments.void_authorization(
                auth_id, actor=principal.label, reason=reason, gate=gate
            )
        except PaymentUncertain as exc:
            self.settle_case_after_void(exc.entity_id)
            raise
        self.settle_case_after_void(op.id)
        return op

    def settle_case_after_void(self, void_id: str) -> None:
        s = self.session
        s.expire_all()
        op = s.get(AuthorizationVoid, void_id)
        if op is None or op.case_id is None:
            return
        case = s.scalar(
            select(ResolutionCase).where(ResolutionCase.id == op.case_id).with_for_update()
        )
        assert case is not None
        if op.status is VoidStatus.COMPLETED and case.status in (
            CaseStatus.EXECUTING,
            CaseStatus.ACTION_REQUIRED,
        ):
            prop = s.get(ResolutionProposal, op.proposal_id) if op.proposal_id else None
            if prop is not None and prop.status is ProposalStatus.ACCEPTED:
                prop.status = ProposalStatus.EXECUTED
                prop.decided_at = datetime.now(UTC)
            self._event(
                case, "void_completed", principal=None, side="system", data={"void_id": op.id}
            )
            case.outcome = "authorization voided (no money had moved)"
            self._move(case, "execution_completed")
        elif (
            op.status in (VoidStatus.UNKNOWN, VoidStatus.FAILED)
            and case.status is CaseStatus.EXECUTING
        ):
            self._event(
                case,
                f"void_{op.status.value}",
                principal=None,
                side="system",
                data={"void_id": op.id},
            )
            self._move(
                case,
                "execution_uncertain" if op.status is VoidStatus.UNKNOWN else "execution_failed",
            )
        s.commit()

    def reconcile_void(self, principal: Principal, void_id: str) -> AuthorizationVoid:
        op = self.session.get(AuthorizationVoid, void_id)
        if op is None:
            raise _fail("NOT_FOUND", "void not found", 404)
        auth = self.session.get(PaymentAuthorization, op.payment_authorization_id)
        assert auth is not None
        inputs, _ = self.void_inputs(principal, auth)
        if not inputs.actor_authorized:
            raise _fail("FORBIDDEN", "reconciling requires a buyer finance role", 403)
        op = self.payments.reconcile_void(void_id)
        self.settle_case_after_void(op.id)
        return op

    # ------------------------------------------------------------ helpers for routes

    def criteria_of(self, case: ResolutionCase) -> list[AcceptanceCriterion]:
        return list(
            self.session.scalars(
                select(AcceptanceCriterion).where(
                    AcceptanceCriterion.agreement_version_id == case.agreement_version_id
                )
            )
        )
