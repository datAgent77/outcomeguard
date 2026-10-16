"""P06 product payment endpoints. Every route requires a session; organization isolation
and roles are enforced in the services. No route here can move money without a fresh
policy evaluation and a human capture approval bound to the evaluated facts."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from outcomeguard.api.deps import AgreementServiceDep, PaymentServiceDep, PrincipalDep
from outcomeguard.api.schemas import CaptureOut, OrderOut
from outcomeguard.auth import Forbidden
from outcomeguard.models import (
    IdempotencyRecord,
    ManualReviewDecision,
    PaymentOrder,
    ReviewDecision,
    Role,
    User,
    VerificationRun,
)
from outcomeguard.services.governance import Governance, GovernanceError

router = APIRouter(tags=["payments"])


class PolicyOut(BaseModel):
    policy_decision_id: str
    decision: str
    reasons: list[dict[str, Any]]
    inputs_sha256: str
    verification_run_id: str | None
    evidence_snapshot_sha256: str | None
    evaluated_at: datetime


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion_id: str
    decision: ReviewDecision
    rationale: str = Field(min_length=10, max_length=4000)
    evidence_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReviewOut(BaseModel):
    id: str
    criterion_id: str
    verification_run_id: str
    decision: str
    rationale: str
    reviewer_user_id: str
    reviewer_email: str | None = None
    reviewer_role: str
    evidence_snapshot_sha256: str
    decided_at: datetime


class ApprovalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inputs_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CaptureIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: str


def _gov(payments) -> Governance:
    return Governance(session=payments.session, payments=payments)


def _policy_out(row) -> PolicyOut:
    return PolicyOut(
        policy_decision_id=row.id,
        decision=row.decision.value,
        reasons=row.reasons,
        inputs_sha256=row.inputs_sha256,
        verification_run_id=row.verification_run_id,
        evidence_snapshot_sha256=row.inputs.get("evidence_snapshot_sha256"),
        evaluated_at=row.evaluated_at,
    )


def _review_out(d: ManualReviewDecision, email: str | None = None) -> ReviewOut:
    return ReviewOut(
        id=d.id,
        criterion_id=d.criterion_id,
        verification_run_id=d.verification_run_id,
        decision=d.decision.value,
        rationale=d.rationale,
        reviewer_user_id=d.reviewer_user_id,
        reviewer_email=email,
        reviewer_role=d.reviewer_role,
        evidence_snapshot_sha256=d.evidence_snapshot_sha256,
        decided_at=d.decided_at,
    )


def _order_out(payments, order: PaymentOrder) -> OrderOut:
    cap = order.authorization.capture if order.authorization else None
    return OrderOut.of(order, payments.refundable_balance(cap) if cap else None)


def _order_for(principal, agreements, payments, order_id: str, *roles: Role) -> PaymentOrder:
    order = payments.session.get(PaymentOrder, order_id)
    if order is None or order.agreement_version_id is None:
        raise GovernanceError("NOT_FOUND", "order not found", http_status=404)
    from outcomeguard.models import AgreementVersion

    version = payments.session.get(AgreementVersion, order.agreement_version_id)
    agreement = agreements.get(principal, version.agreement_id)  # 404 unless readable
    if roles:
        principal.require(agreement.buyer_organization_id, *roles, action="managing payment")
    return order


# ------------------------------------------------------------------ payments


@router.post("/agreements/{agreement_id}/payments", response_model=OrderOut, status_code=201)
def create_payment(
    agreement_id: str,
    principal: PrincipalDep,
    agreements: AgreementServiceDep,
    payments: PaymentServiceDep,
    idempotency_key: Annotated[str | None, Header(pattern=r"^[A-Za-z0-9_-]{8,64}$")] = None,
) -> OrderOut:
    """PayPal order (intent AUTHORIZE) for the agreement's current LOCKED version. Amount,
    currency and merchant come from the version, never from the client.

    With an `Idempotency-Key` header, a repeated request (double click, retry after a
    timeout) returns the order the first request created instead of creating another."""
    principal.require_human("starting a payment")
    agreement = agreements.get(principal, agreement_id)
    principal.require(
        agreement.buyer_organization_id, Role.BUYER_ADMIN, action="starting a payment"
    )
    if agreement.current_locked_version_id is None:
        raise GovernanceError("AGREEMENT_NOT_LOCKED", "lock the agreement before paying")
    from outcomeguard.models import AgreementVersion

    s = payments.session
    record = None
    if idempotency_key:
        scope = f"agreement_payment:{principal.user_id}"
        request_hash = hashlib.sha256(agreement_id.encode()).hexdigest()
        existing = s.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.scope == scope, IdempotencyRecord.key == idempotency_key
            )
        )
        if existing is None:
            record = IdempotencyRecord(key=idempotency_key, scope=scope, request_hash=request_hash)
            s.add(record)
            try:
                s.commit()
            except IntegrityError:
                s.rollback()
                existing = s.scalar(
                    select(IdempotencyRecord).where(
                        IdempotencyRecord.scope == scope, IdempotencyRecord.key == idempotency_key
                    )
                )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise GovernanceError(
                    "IDEMPOTENCY_KEY_REUSED",
                    "key was used for a different request",
                    http_status=422,
                )
            order_id = (existing.response_body or {}).get("order_id")
            if order_id is None:
                raise GovernanceError(
                    "REQUEST_IN_PROGRESS", "the first request with this key has not finished"
                )
            return _order_out(payments, payments._get(PaymentOrder, order_id))

    version = s.get(AgreementVersion, agreement.current_locked_version_id)
    assert version is not None
    try:
        order = payments.create_order_for_agreement(version, actor=principal.label)
    except Exception:
        if record is not None:  # let the client retry with the same key
            s.rollback()
            s.delete(s.merge(record))
            s.commit()
        raise
    if record is not None:
        record = s.merge(record)
        record.response_status = 201
        record.response_body = {"order_id": order.id}
        s.commit()
    return _order_out(payments, order)


@router.get("/payments/orders/{order_id}", response_model=OrderOut)
def get_payment(
    order_id: str,
    principal: PrincipalDep,
    agreements: AgreementServiceDep,
    payments: PaymentServiceDep,
) -> OrderOut:
    order = _order_for(principal, agreements, payments, order_id)
    if order.paypal_order_id and not order.authorization:
        order = payments.sync_order(order, actor=principal.label)  # status read from PayPal
    return _order_out(payments, order)


@router.post("/payments/orders/{order_id}/authorize", response_model=OrderOut)
def authorize_payment(
    order_id: str,
    principal: PrincipalDep,
    agreements: AgreementServiceDep,
    payments: PaymentServiceDep,
) -> OrderOut:
    principal.require_human("authorizing a payment")
    _order_for(principal, agreements, payments, order_id, Role.BUYER_ADMIN, Role.BUYER_APPROVER)
    payments.authorize_order(order_id, actor=principal.label)
    return _order_out(payments, payments._get(PaymentOrder, order_id))


# -------------------------------------------------------------------- policy


@router.get("/payments/authorizations/{auth_id}/policy", response_model=PolicyOut)
def preview_policy(auth_id: str, principal: PrincipalDep, payments: PaymentServiceDep) -> PolicyOut:
    return _policy_out(_gov(payments).preview(principal, auth_id))


# ------------------------------------------------------------- manual review


@router.post(
    "/verification/runs/{run_id}/manual-reviews", response_model=ReviewOut, status_code=201
)
def decide_manual_review(
    run_id: str, body: ReviewIn, principal: PrincipalDep, payments: PaymentServiceDep
) -> ReviewOut:
    d = _gov(payments).record_review(
        principal,
        run_id=run_id,
        criterion_id=body.criterion_id,
        decision=body.decision,
        rationale=body.rationale,
        evidence_snapshot_sha256_seen=body.evidence_snapshot_sha256,
    )
    return _review_out(d)


@router.get("/verification/runs/{run_id}/manual-reviews", response_model=list[ReviewOut])
def list_manual_reviews(
    run_id: str,
    principal: PrincipalDep,
    agreements: AgreementServiceDep,
    payments: PaymentServiceDep,
) -> list[ReviewOut]:
    s = payments.session
    run = s.get(VerificationRun, run_id)
    if run is None:
        raise GovernanceError("NOT_FOUND", "run not found", http_status=404)
    from outcomeguard.models import AgreementVersion

    version = s.get(AgreementVersion, run.agreement_version_id)
    assert version is not None
    agreements.get(principal, version.agreement_id)
    rows = s.execute(
        select(ManualReviewDecision, User.email)
        .join(User, User.id == ManualReviewDecision.reviewer_user_id)
        .where(ManualReviewDecision.verification_run_id == run_id)
    ).all()
    return [_review_out(d, email) for d, email in rows]


# ------------------------------------------------------------------- capture


@router.post("/payments/authorizations/{auth_id}/capture-approvals", status_code=201)
def approve_capture(
    auth_id: str, body: ApprovalIn, principal: PrincipalDep, payments: PaymentServiceDep
) -> dict:
    a = _gov(payments).approve_capture(principal, auth_id, body.inputs_sha256)
    return {
        "approval_id": a.id,
        "inputs_sha256": a.inputs_sha256,
        "policy_decision_id": a.policy_decision_id,
        "approved_at": a.approved_at,
    }


@router.post("/payments/authorizations/{auth_id}/captures", response_model=CaptureOut)
def capture(
    auth_id: str, body: CaptureIn, principal: PrincipalDep, payments: PaymentServiceDep
) -> CaptureOut:
    cap = _gov(payments).capture(principal, auth_id, body.approval_id)
    return CaptureOut.of(cap, payments.environment.value, payments.refundable_balance(cap))


@router.post("/payments/captures/{capture_id}/reconcile", response_model=CaptureOut)
def reconcile(capture_id: str, principal: PrincipalDep, payments: PaymentServiceDep) -> CaptureOut:
    cap = _gov(payments).reconcile(principal, capture_id)
    return CaptureOut.of(cap, payments.environment.value, payments.refundable_balance(cap))


__all__ = ["Forbidden", "router"]
