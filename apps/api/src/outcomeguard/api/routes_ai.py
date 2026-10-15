"""AI endpoints: agreement drafting (buyer) and evidence explanations (buyer/merchant).

None of these endpoints can lock an agreement, approve anything, or touch payments.
"""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from outcomeguard.ai.agreements import AgreementIntelligence
from outcomeguard.ai.evidence import EvidenceIntelligence
from outcomeguard.ai.gateway import AIGateway, build_provider
from outcomeguard.ai.provider import AIProvider
from outcomeguard.api.deps import AgreementServiceDep, PrincipalDep, SessionDep, SettingsDep
from outcomeguard.config import get_settings
from outcomeguard.models import AgreementVersion, VerificationRun
from outcomeguard.models.ai import AgreementDraft, EvidenceAnalysis
from outcomeguard.money import Money, MoneyError
from outcomeguard.services.agreements import AgreementError

router = APIRouter(tags=["ai"])


@lru_cache
def _provider() -> AIProvider | None:
    return build_provider(get_settings())


def get_ai_provider() -> AIProvider | None:
    return _provider()


ProviderDep = Annotated[AIProvider | None, Depends(get_ai_provider)]


def _gateway(session, provider, settings) -> AIGateway:
    return AIGateway(session=session, provider=provider, settings=settings)


class DraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    buyer_organization_id: str
    requirements_text: str = Field(min_length=1, max_length=5000)


class DraftOut(BaseModel):
    id: str
    status: str
    requirements_text: str
    ai_call_id: str | None
    review: dict[str, Any]
    accepted_agreement_id: str | None
    created_at: datetime

    @classmethod
    def of(cls, d: AgreementDraft) -> DraftOut:
        return cls(
            id=d.id,
            status=d.status.value,
            requirements_text=d.requirements_text,
            ai_call_id=d.ai_call_id,
            review=d.review,
            accepted_agreement_id=d.accepted_agreement_id,
            created_at=d.created_at,
        )


class AcceptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merchant_account_id: str
    title: str = Field(min_length=1, max_length=200)
    amount: str = Field(pattern=r"^\d{1,7}(\.\d{1,2})?$")
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    delivery_deadline: datetime
    include_keys: list[str] | None = None
    configuration: dict[str, dict[str, Any]] = Field(default_factory=dict)
    use_standard_form_fields: bool = False
    # Buyer decision, audited: e.g. a manual-review item checked outside OutcomeGuard.
    blocking_overrides: dict[str, bool] = Field(default_factory=dict)


class AnalysisOut(BaseModel):
    id: str
    run_id: str
    status: str
    run_verdict: str | None
    ai_call_id: str | None
    grounded: dict[str, Any]
    created_at: datetime

    @classmethod
    def of(cls, a: EvidenceAnalysis) -> AnalysisOut:
        return cls(
            id=a.id,
            run_id=a.run_id,
            status=a.status.value,
            run_verdict=a.run_verdict,
            ai_call_id=a.ai_call_id,
            grounded=a.grounded,
            created_at=a.created_at,
        )


@router.post("/agreements/drafts", response_model=DraftOut, status_code=201)
def create_draft(
    body: DraftIn,
    principal: PrincipalDep,
    session: SessionDep,
    settings: SettingsDep,
    provider: ProviderDep,
) -> DraftOut:
    intel = AgreementIntelligence(session, _gateway(session, provider, settings))
    return DraftOut.of(
        intel.draft(
            principal,
            buyer_organization_id=body.buyer_organization_id,
            requirements_text=body.requirements_text,
        )
    )


@router.get("/agreements/drafts/{draft_id}", response_model=DraftOut)
def get_draft(draft_id: str, principal: PrincipalDep, session: SessionDep) -> DraftOut:
    d = session.get(AgreementDraft, draft_id)
    if d is None or not principal.member_of(d.buyer_organization_id):
        raise HTTPException(404, "not found")
    return DraftOut.of(d)


@router.post("/agreements/drafts/{draft_id}/accept", status_code=201)
def accept_draft(
    draft_id: str,
    body: AcceptIn,
    principal: PrincipalDep,
    session: SessionDep,
    settings: SettingsDep,
    provider: ProviderDep,
) -> dict:
    try:
        amount = Money.from_decimal_str(body.amount, body.currency)
    except MoneyError as exc:
        raise AgreementError("INVALID_AMOUNT", str(exc), http_status=422) from exc
    intel = AgreementIntelligence(session, _gateway(session, provider, settings))
    a = intel.accept(
        principal,
        draft_id,
        merchant_account_id=body.merchant_account_id,
        title=body.title,
        amount=amount,
        delivery_deadline=body.delivery_deadline,
        configuration=body.configuration,
        include_keys=body.include_keys,
        use_standard_form_fields=body.use_standard_form_fields,
        blocking_overrides=body.blocking_overrides,
    )
    # The agreement is a DRAFT: the buyer must still propose and approve it to lock.
    return {
        "agreement_id": a.id,
        "status": a.status.value,
        "version": 1,
        "next": "review the criteria, then POST /agreements/{id}/versions/1/propose and "
        "/approve (buyer approver)",
    }


@router.post("/agreements/drafts/{draft_id}/discard", response_model=DraftOut)
def discard_draft(
    draft_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    settings: SettingsDep,
    provider: ProviderDep,
) -> DraftOut:
    intel = AgreementIntelligence(session, _gateway(session, provider, settings))
    return DraftOut.of(intel.discard(principal, draft_id))


def _readable_run(session, svc, principal, run_id: str) -> VerificationRun:
    run = session.get(VerificationRun, run_id)
    if run is None:
        raise HTTPException(404, "not found")
    version = session.get(AgreementVersion, run.agreement_version_id)
    svc.get(principal, version.agreement_id)  # 404 unless buyer/merchant may read
    return run


@router.post("/verification/runs/{run_id}/analysis", response_model=AnalysisOut, status_code=201)
def analyze_run(
    run_id: str,
    principal: PrincipalDep,
    svc: AgreementServiceDep,
    session: SessionDep,
    settings: SettingsDep,
    provider: ProviderDep,
) -> AnalysisOut:
    _readable_run(session, svc, principal, run_id)
    intel = EvidenceIntelligence(session, _gateway(session, provider, settings))
    return AnalysisOut.of(intel.analyze(run_id, actor=principal.label))


@router.get("/verification/runs/{run_id}/analyses", response_model=list[AnalysisOut])
def list_analyses(
    run_id: str, principal: PrincipalDep, svc: AgreementServiceDep, session: SessionDep
) -> list[AnalysisOut]:
    _readable_run(session, svc, principal, run_id)
    rows = session.scalars(
        select(EvidenceAnalysis)
        .where(EvidenceAnalysis.run_id == run_id)
        .order_by(EvidenceAnalysis.created_at.desc())
    ).all()
    return [AnalysisOut.of(a) for a in rows]
