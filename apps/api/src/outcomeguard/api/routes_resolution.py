"""P08 resolution, refund and void endpoints. Sessions required; organization isolation
(404) and roles are enforced in `services/resolution.py`. Financial mutations take an
`Idempotency-Key` (refund: required)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from outcomeguard.api.deps import AgreementServiceDep, PaymentServiceDep, PrincipalDep
from outcomeguard.api.schemas import RefundOut
from outcomeguard.models import (
    ApprovalChoice,
    AuthorizationVoid,
    FinancialPolicyDecision,
    PaymentCapture,
    PaymentRefund,
    ReasonCategory,
    Remedy,
    ResolutionApproval,
    ResolutionCase,
    ResolutionEvent,
    ResolutionProposal,
    User,
)
from outcomeguard.money import Money, MoneyError
from outcomeguard.services.governance import GovernanceError
from outcomeguard.services.resolution import Resolution

router = APIRouter(tags=["resolution"])

Amount = Field(pattern=r"^\d{1,7}(\.\d{1,2})?$")
IdemKey = Annotated[str, Header(pattern=r"^[A-Za-z0-9_-]{8,64}$")]


def _res(payments) -> Resolution:
    return Resolution(session=payments.session, payments=payments)


def _money(amount: str | None, currency: str) -> Money | None:
    if amount is None:
        return None
    try:
        return Money.from_decimal_str(amount, currency)
    except MoneyError as exc:
        raise GovernanceError("REFUND_AMOUNT_INVALID", str(exc), http_status=422) from exc


def _amt(minor: int | None, currency: str) -> str | None:
    return None if minor is None else Money(minor, currency).to_decimal_str()


# ------------------------------------------------------------------ schemas


class OpenCaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason_category: ReasonCategory
    description: str = Field(min_length=10, max_length=5000)
    requested_remedy: Remedy
    requested_amount: str | None = Amount
    criterion_ids: list[str] = Field(default_factory=list, max_length=20)
    artifact_ids: list[str] = Field(default_factory=list, max_length=50)


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=5000)


class EvidenceLinkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verification_run_id: str
    note: str | None = Field(default=None, max_length=2000)


class ProposalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int
    remedy: Remedy
    amount: str | None = Amount
    rationale: str = Field(min_length=10, max_length=4000)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    approve: bool = False


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    note: str | None = Field(default=None, max_length=2000)


class CloseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: str = Field(pattern=r"^(resolved|closed_no_action|cancelled|rejected)$")
    reason: str = Field(min_length=5, max_length=2000)


class RefundIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str


class VoidIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=5, max_length=2000)


class EventOut(BaseModel):
    id: str
    kind: str
    side: str | None
    author_email: str | None
    body: str | None
    data: dict[str, Any]
    created_at: datetime


class ApprovalOut(BaseModel):
    side: str
    choice: str
    approver_email: str | None
    approver_role: str
    proposal_content_sha256: str
    decided_at: datetime


class ProposalOut(BaseModel):
    id: str
    number: int
    side: str
    author_email: str | None
    remedy: str
    amount: str | None
    currency: str
    rationale: str
    evidence_refs: list[Any]
    content_sha256: str
    status: str
    created_at: datetime
    decided_at: datetime | None
    approvals: list[ApprovalOut]


class FinancialsOut(BaseModel):
    """PayPal-reported where noted; held = reserved/in-flight/uncertain refunds."""

    currency: str
    captured: str | None
    refunded: str
    held: str
    remaining: str | None
    paypal_fee_at_capture: str | None
    refunds: list[RefundOut]


class CaseOut(BaseModel):
    id: str
    agreement_id: str
    agreement_version_id: str
    agreement_content_hash: str
    payment_authorization_id: str | None
    payment_capture_id: str | None
    status: str
    revision: int
    reason_category: str
    description: str
    requested_remedy: str
    requested_amount: str | None
    currency: str
    opened_by_email: str | None
    your_side: str | None
    evidence_snapshot: dict[str, Any]
    evidence_snapshot_sha256: str
    outcome: str | None
    closure_reason: str | None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    proposals: list[ProposalOut]
    events: list[EventOut]
    financials: FinancialsOut | None


class CaseSummary(BaseModel):
    id: str
    status: str
    reason_category: str
    requested_remedy: str
    requested_amount: str | None
    currency: str
    created_at: datetime
    closed_at: datetime | None
    outcome: str | None


class PolicyOut(BaseModel):
    policy_decision_id: str
    purpose: str
    decision: str
    reasons: list[dict[str, Any]]
    inputs_sha256: str
    figures: dict[str, Any]
    proposal_id: str | None
    evaluated_at: datetime


class VoidOut(BaseModel):
    id: str
    payment_authorization_id: str
    status: str
    reason: str
    requested_by: str
    paypal_status: str | None
    case_id: str | None
    error: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


def _email(s, user_id: str | None) -> str | None:
    if not user_id:
        return None
    u = s.get(User, user_id)
    return u.email if u else None


def _case_out(res: Resolution, case: ResolutionCase, side) -> CaseOut:
    s = res.session
    proposals = s.scalars(
        select(ResolutionProposal)
        .where(ResolutionProposal.case_id == case.id)
        .order_by(ResolutionProposal.number)
    ).all()
    events = s.scalars(
        select(ResolutionEvent)
        .where(ResolutionEvent.case_id == case.id)
        .order_by(ResolutionEvent.created_at, ResolutionEvent.id)
    ).all()
    fin = None
    if case.payment_capture_id:
        cap = s.get(PaymentCapture, case.payment_capture_id)
        assert cap is not None
        refunded = sum(r.amount_minor for r in cap.refunds if r.status.value == "completed")
        remaining = res.payments.refundable_balance(cap).minor
        held = cap.amount_minor - remaining - refunded
        env = res.payments.environment.value
        fin = FinancialsOut(
            currency=cap.currency,
            captured=_amt(cap.amount_minor, cap.currency),
            refunded=Money(refunded, cap.currency).to_decimal_str(),
            held=Money(held, cap.currency).to_decimal_str(),
            remaining=Money(remaining, cap.currency).to_decimal_str(),
            paypal_fee_at_capture=_amt(cap.paypal_fee_minor, cap.currency),
            refunds=[RefundOut.of(r, env) for r in cap.refunds],
        )
    return CaseOut(
        id=case.id,
        agreement_id=case.agreement_id,
        agreement_version_id=case.agreement_version_id,
        agreement_content_hash=case.agreement_content_hash,
        payment_authorization_id=case.payment_authorization_id,
        payment_capture_id=case.payment_capture_id,
        status=case.status.value,
        revision=case.revision,
        reason_category=case.reason_category.value,
        description=case.description,
        requested_remedy=case.requested_remedy.value,
        requested_amount=_amt(case.requested_amount_minor, case.currency),
        currency=case.currency,
        opened_by_email=_email(s, case.opened_by_user_id),
        your_side=side.value if side else None,
        evidence_snapshot=case.evidence_snapshot,
        evidence_snapshot_sha256=case.evidence_snapshot_sha256,
        outcome=case.outcome,
        closure_reason=case.closure_reason,
        created_at=case.created_at,
        updated_at=case.updated_at,
        closed_at=case.closed_at,
        proposals=[
            ProposalOut(
                id=p.id,
                number=p.number,
                side=p.side.value,
                author_email=_email(s, p.author_user_id),
                remedy=p.remedy.value,
                amount=_amt(p.amount_minor, p.currency),
                currency=p.currency,
                rationale=p.rationale,
                evidence_refs=p.evidence_refs,
                content_sha256=p.content_sha256,
                status=p.status.value,
                created_at=p.created_at,
                decided_at=p.decided_at,
                approvals=[
                    ApprovalOut(
                        side=a.side.value,
                        choice=a.choice.value,
                        approver_email=_email(s, a.approver_user_id),
                        approver_role=a.approver_role,
                        proposal_content_sha256=a.proposal_content_sha256,
                        decided_at=a.decided_at,
                    )
                    for a in s.scalars(
                        select(ResolutionApproval).where(ResolutionApproval.proposal_id == p.id)
                    )
                ],
            )
            for p in proposals
        ],
        events=[
            EventOut(
                id=e.id,
                kind=e.kind,
                side=e.side,
                author_email=_email(s, e.author_user_id),
                body=e.body,
                data=e.data,
                created_at=e.created_at,
            )
            for e in events
        ],
        financials=fin,
    )


def _policy_out(row: FinancialPolicyDecision) -> PolicyOut:
    return PolicyOut(
        policy_decision_id=row.id,
        purpose=row.purpose.value,
        decision=row.decision.value,
        reasons=row.reasons,
        inputs_sha256=row.inputs_sha256,
        figures=row.inputs.get("figures", {}),
        proposal_id=row.proposal_id,
        evaluated_at=row.evaluated_at,
    )


def _void_out(v: AuthorizationVoid) -> VoidOut:
    return VoidOut(
        id=v.id,
        payment_authorization_id=v.payment_authorization_id,
        status=v.status.value,
        reason=v.reason,
        requested_by=v.requested_by,
        paypal_status=v.paypal_status,
        case_id=v.case_id,
        error=v.error,
        created_at=v.created_at,
        updated_at=v.updated_at,
    )


def _view(res: Resolution, principal, case_id: str) -> CaseOut:
    res.session.expire_all()
    v = res.get(principal, case_id)
    return _case_out(res, v.case, v.side)


# ------------------------------------------------------------------- routes


@router.post("/agreements/{agreement_id}/resolutions", response_model=CaseOut, status_code=201)
def open_case(
    agreement_id: str,
    body: OpenCaseIn,
    principal: PrincipalDep,
    agreements: AgreementServiceDep,
    payments: PaymentServiceDep,
) -> CaseOut:
    agreement = agreements.get(principal, agreement_id)
    res = _res(payments)
    version = (
        next(v for v in agreement.versions if v.id == agreement.current_locked_version_id)
        if agreement.current_locked_version_id
        else None
    )
    currency = version.currency if version else "USD"
    case = res.open_case(
        principal,
        agreement,
        reason_category=body.reason_category,
        description=body.description,
        requested_remedy=body.requested_remedy,
        requested_amount=_money(body.requested_amount, currency),
        criterion_ids=body.criterion_ids,
        artifact_ids=body.artifact_ids,
    )
    return _view(res, principal, case.id)


@router.get("/agreements/{agreement_id}/resolutions", response_model=list[CaseSummary])
def list_cases(
    agreement_id: str,
    principal: PrincipalDep,
    agreements: AgreementServiceDep,
    payments: PaymentServiceDep,
) -> list[CaseSummary]:
    agreement = agreements.get(principal, agreement_id)
    return [
        CaseSummary(
            id=c.id,
            status=c.status.value,
            reason_category=c.reason_category.value,
            requested_remedy=c.requested_remedy.value,
            requested_amount=_amt(c.requested_amount_minor, c.currency),
            currency=c.currency,
            created_at=c.created_at,
            closed_at=c.closed_at,
            outcome=c.outcome,
        )
        for c in _res(payments).list_for_agreement(principal, agreement)
    ]


@router.get("/resolutions/{case_id}", response_model=CaseOut)
def get_case(case_id: str, principal: PrincipalDep, payments: PaymentServiceDep) -> CaseOut:
    return _view(_res(payments), principal, case_id)


@router.post("/resolutions/{case_id}/messages", response_model=CaseOut)
def message(
    case_id: str, body: MessageIn, principal: PrincipalDep, payments: PaymentServiceDep
) -> CaseOut:
    res = _res(payments)
    res.post_message(principal, case_id, body.body)
    return _view(res, principal, case_id)


@router.post("/resolutions/{case_id}/evidence", response_model=CaseOut)
def evidence(
    case_id: str, body: EvidenceLinkIn, principal: PrincipalDep, payments: PaymentServiceDep
) -> CaseOut:
    res = _res(payments)
    res.link_evidence(principal, case_id, body.verification_run_id, body.note)
    return _view(res, principal, case_id)


@router.post("/resolutions/{case_id}/proposals", response_model=CaseOut, status_code=201)
def propose(
    case_id: str, body: ProposalIn, principal: PrincipalDep, payments: PaymentServiceDep
) -> CaseOut:
    res = _res(payments)
    case = res.get(principal, case_id).case
    res.submit_proposal(
        principal,
        case_id,
        expected_revision=body.expected_revision,
        remedy=body.remedy,
        amount=_money(body.amount, case.currency),
        rationale=body.rationale,
        evidence_refs=body.evidence_refs,
        approve=body.approve,
    )
    return _view(res, principal, case_id)


@router.post("/resolutions/{case_id}/proposals/{proposal_id}/approve", response_model=CaseOut)
def approve(
    case_id: str,
    proposal_id: str,
    body: DecisionIn,
    principal: PrincipalDep,
    payments: PaymentServiceDep,
) -> CaseOut:
    res = _res(payments)
    res.decide(
        principal,
        case_id,
        proposal_id,
        choice=ApprovalChoice.APPROVE,
        content_sha256=body.content_sha256,
        note=body.note,
    )
    return _view(res, principal, case_id)


@router.post("/resolutions/{case_id}/proposals/{proposal_id}/reject", response_model=CaseOut)
def reject(
    case_id: str,
    proposal_id: str,
    body: DecisionIn,
    principal: PrincipalDep,
    payments: PaymentServiceDep,
) -> CaseOut:
    res = _res(payments)
    res.decide(
        principal,
        case_id,
        proposal_id,
        choice=ApprovalChoice.REJECT,
        content_sha256=body.content_sha256,
        note=body.note,
    )
    return _view(res, principal, case_id)


@router.post("/resolutions/{case_id}/proposals/{proposal_id}/withdraw", response_model=CaseOut)
def withdraw(
    case_id: str, proposal_id: str, principal: PrincipalDep, payments: PaymentServiceDep
) -> CaseOut:
    res = _res(payments)
    res.withdraw_proposal(principal, case_id, proposal_id)
    return _view(res, principal, case_id)


@router.post("/resolutions/{case_id}/close", response_model=CaseOut)
def close(
    case_id: str, body: CloseIn, principal: PrincipalDep, payments: PaymentServiceDep
) -> CaseOut:
    res = _res(payments)
    res.close(principal, case_id, outcome=body.outcome, reason=body.reason)
    return _view(res, principal, case_id)


@router.get("/resolutions/{case_id}/policy", response_model=PolicyOut)
def refund_policy(
    case_id: str,
    principal: PrincipalDep,
    payments: PaymentServiceDep,
    proposal_id: str | None = None,
) -> PolicyOut:
    """Fresh evaluation of the refund policy for the case's latest (or given) proposal.
    Persisted as evidence; it moves no money."""
    return _policy_out(_res(payments).refund_policy(principal, case_id, proposal_id))


@router.post("/resolutions/{case_id}/refunds", response_model=RefundOut)
def execute_refund(
    case_id: str,
    body: RefundIn,
    principal: PrincipalDep,
    payments: PaymentServiceDep,
    idempotency_key: IdemKey,
) -> RefundOut:
    r = _res(payments).execute_refund(principal, case_id, body.proposal_id, idempotency_key)
    payments.session.expire_all()
    return RefundOut.of(payments.session.get(PaymentRefund, r.id), payments.environment.value)  # type: ignore[arg-type]


@router.get("/resolutions/{case_id}/refunds", response_model=list[RefundOut])
def list_refunds(
    case_id: str, principal: PrincipalDep, payments: PaymentServiceDep
) -> list[RefundOut]:
    res = _res(payments)
    case = res.get(principal, case_id).case
    rows = payments.session.scalars(
        select(PaymentRefund)
        .where(PaymentRefund.resolution_case_id == case.id)
        .order_by(PaymentRefund.created_at)
    ).all()
    return [RefundOut.of(r, payments.environment.value) for r in rows]


@router.post("/refunds/{refund_id}/reconcile", response_model=RefundOut)
def reconcile_refund(
    refund_id: str, principal: PrincipalDep, payments: PaymentServiceDep
) -> RefundOut:
    r = _res(payments).reconcile_refund(principal, refund_id)
    return RefundOut.of(r, payments.environment.value)


@router.get("/payments/authorizations/{auth_id}/void-policy", response_model=PolicyOut)
def void_policy(auth_id: str, principal: PrincipalDep, payments: PaymentServiceDep) -> PolicyOut:
    return _policy_out(_res(payments).void_policy(principal, auth_id))


@router.post("/payments/authorizations/{auth_id}/void", response_model=VoidOut)
def void(
    auth_id: str, body: VoidIn, principal: PrincipalDep, payments: PaymentServiceDep
) -> VoidOut:
    """Cancel an authorized, uncaptured payment. Idempotent per authorization (one
    operation, deterministic PayPal-Request-Id)."""
    return _void_out(_res(payments).void(principal, auth_id, reason=body.reason))


@router.get("/payments/authorizations/{auth_id}/void-status", response_model=VoidOut | None)
def void_status(
    auth_id: str, principal: PrincipalDep, payments: PaymentServiceDep
) -> VoidOut | None:
    from outcomeguard.models import PaymentAuthorization

    res = _res(payments)
    auth = payments.session.get(PaymentAuthorization, auth_id)
    if auth is None:
        raise GovernanceError("NOT_FOUND", "authorization not found", http_status=404)
    res.void_inputs(principal, auth)  # 404 for outsiders
    op = payments.session.scalar(
        select(AuthorizationVoid).where(AuthorizationVoid.payment_authorization_id == auth_id)
    )
    return _void_out(op) if op else None


@router.post("/voids/{void_id}/reconcile", response_model=VoidOut)
def reconcile_void(void_id: str, principal: PrincipalDep, payments: PaymentServiceDep) -> VoidOut:
    return _void_out(_res(payments).reconcile_void(principal, void_id))
