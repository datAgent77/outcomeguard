"""Agreement API. Every route requires a session (Bearer token); roles are checked in
the service. Version mutations take `expected_revision` (optimistic concurrency)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from outcomeguard.api.deps import AgreementServiceDep, PrincipalDep
from outcomeguard.domain.criteria import CriterionInput
from outcomeguard.models import (
    AcceptanceCriterion,
    Agreement,
    AgreementVersion,
    Approval,
    ApprovalSubject,
)
from outcomeguard.money import Money, MoneyError
from outcomeguard.services.agreements import AgreementError, DraftContent

router = APIRouter(prefix="/agreements", tags=["agreements"])

AmountStr = Field(pattern=r"^\d{1,7}(\.\d{1,2})?$", examples=["500.00"])


# ------------------------------------------------------------------ schemas


class CriterionOut(BaseModel):
    id: str
    position: int
    key: str
    title: str
    kind: str
    verification_method: str
    blocking: bool
    source: str
    spec: dict[str, Any]

    @classmethod
    def of(cls, c: AcceptanceCriterion) -> CriterionOut:
        return cls(
            id=c.id,
            position=c.position,
            key=c.key,
            title=c.title,
            kind=c.kind.value,
            verification_method=c.verification_method.value,
            blocking=c.blocking,
            source=c.source.value,
            spec=c.spec,
        )


class ApprovalOut(BaseModel):
    id: str
    decision: str
    approver_user_id: str
    approver_role: str
    content_hash: str
    decided_at: datetime
    note: str | None


class VersionOut(BaseModel):
    id: str
    version_number: int
    status: str
    revision: int
    supersedes_version_id: str | None
    title: str
    description: str
    requirements_text: str
    amount: str
    currency: str
    delivery_deadline: datetime
    content_hash: str | None
    proposed_at: datetime | None
    locked_at: datetime | None
    criteria: list[CriterionOut]
    approvals: list[ApprovalOut]

    @classmethod
    def of(cls, v: AgreementVersion, approvals: list[Approval]) -> VersionOut:
        return cls(
            id=v.id,
            version_number=v.version_number,
            status=v.status.value,
            revision=v.revision,
            supersedes_version_id=v.supersedes_version_id,
            title=v.title,
            description=v.description,
            requirements_text=v.requirements_text,
            amount=Money(v.amount_minor, v.currency).to_decimal_str(),
            currency=v.currency,
            delivery_deadline=v.delivery_deadline,
            content_hash=v.content_hash,
            proposed_at=v.proposed_at,
            locked_at=v.locked_at,
            criteria=[CriterionOut.of(c) for c in v.criteria],
            approvals=[
                ApprovalOut(
                    id=a.id,
                    decision=a.decision.value,
                    approver_user_id=a.approver_user_id,
                    approver_role=a.approver_role,
                    content_hash=a.content_hash,
                    decided_at=a.decided_at,
                    note=a.note,
                )
                for a in approvals
            ],
        )


class AgreementOut(BaseModel):
    id: str
    status: str
    service_category: str
    buyer_organization_id: str
    merchant_account_id: str
    current_locked_version_id: str | None
    created_at: datetime
    versions: list[VersionOut]


class CreateAgreementIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    buyer_organization_id: str
    merchant_account_id: str
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    requirements_text: str = Field(min_length=1, max_length=5000)
    amount: str = AmountStr
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    delivery_deadline: datetime
    use_landing_page_template: bool = True
    template_min_performance: int = Field(default=90, ge=0, le=100)
    criteria: list[CriterionInput] | None = None


class UpdateDraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    requirements_text: str | None = Field(default=None, min_length=1, max_length=5000)
    amount: str | None = Field(default=None, pattern=r"^\d{1,7}(\.\d{1,2})?$")
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    delivery_deadline: datetime | None = None


class ReplaceCriteriaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int
    criteria: list[CriterionInput] = Field(max_length=20)


class RevisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int


class ApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    note: str | None = Field(default=None, max_length=500)


# ------------------------------------------------------------------- helpers


def _money(amount: str, currency: str) -> Money:
    try:
        return Money.from_decimal_str(amount, currency)
    except MoneyError as exc:
        raise AgreementError("INVALID_AMOUNT", str(exc), http_status=422) from exc


def _out(svc: AgreementServiceDep, a: Agreement) -> AgreementOut:
    from sqlalchemy import select

    ids = [v.id for v in a.versions]
    approvals = list(
        svc.session.scalars(
            select(Approval).where(
                Approval.subject_type == ApprovalSubject.AGREEMENT_VERSION,
                Approval.subject_id.in_(ids),
            )
        )
    )
    return AgreementOut(
        id=a.id,
        status=a.status.value,
        service_category=a.service_category.value,
        buyer_organization_id=a.buyer_organization_id,
        merchant_account_id=a.merchant_account_id,
        current_locked_version_id=a.current_locked_version_id,
        created_at=a.created_at,
        versions=[
            VersionOut.of(v, [x for x in approvals if x.subject_id == v.id]) for v in a.versions
        ],
    )


def _reload(svc: AgreementServiceDep, principal: PrincipalDep, agreement_id: str) -> AgreementOut:
    svc.session.expire_all()
    return _out(svc, svc.get(principal, agreement_id))


# -------------------------------------------------------------------- routes


@router.post("", response_model=AgreementOut, status_code=201)
def create_agreement(
    body: CreateAgreementIn, principal: PrincipalDep, svc: AgreementServiceDep
) -> AgreementOut:
    a = svc.create(
        principal,
        buyer_organization_id=body.buyer_organization_id,
        merchant_account_id=body.merchant_account_id,
        content=DraftContent(
            title=body.title,
            description=body.description,
            requirements_text=body.requirements_text,
            amount=_money(body.amount, body.currency),
            delivery_deadline=body.delivery_deadline,
        ),
        criteria=body.criteria,
        use_template=body.criteria is None and body.use_landing_page_template,
        template_min_performance=body.template_min_performance,
    )
    return _reload(svc, principal, a.id)


@router.get("", response_model=list[AgreementOut])
def list_agreements(principal: PrincipalDep, svc: AgreementServiceDep) -> list[AgreementOut]:
    return [_out(svc, a) for a in svc.list_visible(principal)]


@router.get("/{agreement_id}", response_model=AgreementOut)
def get_agreement(
    agreement_id: str, principal: PrincipalDep, svc: AgreementServiceDep
) -> AgreementOut:
    return _out(svc, svc.get(principal, agreement_id))


@router.patch("/{agreement_id}/versions/{n}", response_model=AgreementOut)
def update_draft(
    agreement_id: str,
    n: int,
    body: UpdateDraftIn,
    principal: PrincipalDep,
    svc: AgreementServiceDep,
) -> AgreementOut:
    changes: dict[str, Any] = {}
    for f in ("title", "description", "requirements_text", "delivery_deadline"):
        if (v := getattr(body, f)) is not None:
            changes[f] = v
    if body.amount is not None or body.currency is not None:
        current = svc.get_version(principal, agreement_id, n)
        money = _money(
            body.amount or Money(current.amount_minor, current.currency).to_decimal_str(),
            body.currency or current.currency,
        )
        changes["amount_minor"] = money.minor
        changes["currency"] = money.currency
    svc.update_draft(
        principal, agreement_id, n, expected_revision=body.expected_revision, changes=changes
    )
    return _reload(svc, principal, agreement_id)


@router.put("/{agreement_id}/versions/{n}/criteria", response_model=AgreementOut)
def replace_criteria(
    agreement_id: str,
    n: int,
    body: ReplaceCriteriaIn,
    principal: PrincipalDep,
    svc: AgreementServiceDep,
) -> AgreementOut:
    svc.replace_criteria(
        principal, agreement_id, n, expected_revision=body.expected_revision, criteria=body.criteria
    )
    return _reload(svc, principal, agreement_id)


@router.post("/{agreement_id}/versions/{n}/propose", response_model=AgreementOut)
def propose(
    agreement_id: str, n: int, body: RevisionIn, principal: PrincipalDep, svc: AgreementServiceDep
) -> AgreementOut:
    svc.propose(principal, agreement_id, n, expected_revision=body.expected_revision)
    return _reload(svc, principal, agreement_id)


@router.post("/{agreement_id}/versions/{n}/withdraw", response_model=AgreementOut)
def withdraw(
    agreement_id: str, n: int, body: RevisionIn, principal: PrincipalDep, svc: AgreementServiceDep
) -> AgreementOut:
    svc.withdraw(principal, agreement_id, n, expected_revision=body.expected_revision)
    return _reload(svc, principal, agreement_id)


@router.post("/{agreement_id}/versions/{n}/approve", response_model=AgreementOut)
def approve(
    agreement_id: str, n: int, body: ApproveIn, principal: PrincipalDep, svc: AgreementServiceDep
) -> AgreementOut:
    svc.approve(principal, agreement_id, n, content_hash_seen=body.content_hash, note=body.note)
    return _reload(svc, principal, agreement_id)


@router.post("/{agreement_id}/versions/{n}/discard", response_model=AgreementOut)
def discard(
    agreement_id: str, n: int, body: RevisionIn, principal: PrincipalDep, svc: AgreementServiceDep
) -> AgreementOut:
    svc.discard(principal, agreement_id, n, expected_revision=body.expected_revision)
    return _reload(svc, principal, agreement_id)


@router.post("/{agreement_id}/versions", response_model=AgreementOut, status_code=201)
def new_version(
    agreement_id: str, principal: PrincipalDep, svc: AgreementServiceDep
) -> AgreementOut:
    svc.new_version(principal, agreement_id)
    return _reload(svc, principal, agreement_id)
