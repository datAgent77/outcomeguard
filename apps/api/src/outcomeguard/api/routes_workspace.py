"""Read-only views for the product UI (P07).

Nothing here mutates state or evaluates policy: dashboards show the *last persisted*
policy decision with its timestamp; a fresh evaluation is only ever made by the P06
policy endpoint. Every route is organization-scoped through `AgreementService` (404 for
anything the caller may not read).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import false, func, or_, select
from sqlalchemy.orm import Session

from outcomeguard.api.deps import AgreementServiceDep, PaymentServiceDep, PrincipalDep, SessionDep
from outcomeguard.api.schemas import OrderOut
from outcomeguard.models import (
    AcceptanceCriterion,
    Agreement,
    AgreementVersion,
    AuthorizationVoid,
    CaptureApproval,
    EvidenceSubmission,
    FinancialPolicyDecision,
    ManualReviewDecision,
    MerchantAccount,
    Organization,
    PaymentAuthorization,
    PaymentCapture,
    PaymentOrder,
    PaymentRefund,
    PolicyDecisionRecord,
    ResolutionCase,
    User,
    UserKind,
    VerificationRun,
)
from outcomeguard.models.ai import AgreementDraft, EvidenceAnalysis
from outcomeguard.models.audit import AuditEvent
from outcomeguard.money import Money
from outcomeguard.services.governance import GovernanceError

router = APIRouter(tags=["workspace"])

# The governance demo writes this prefix into the rationale of reviews it records through
# a demo account (P06). It is the only persisted marker of a scripted decision.
SCRIPTED_MARKER = "SCRIPTED DEMO DECISION"
DEMO_EMAIL_SUFFIXES = (".demo", "@system.local")


# --------------------------------------------------------------------- directory


class OrgOut(BaseModel):
    id: str
    name: str
    kind: str
    roles: list[str]


class ContextOut(BaseModel):
    user_id: str
    email: str
    display_name: str
    kind: str
    demo_account: bool
    organizations: list[OrgOut]


class MerchantOut(BaseModel):
    id: str
    display_name: str
    organization_id: str
    organization_name: str
    environment: str


def _is_demo(email: str) -> bool:
    return email.endswith(DEMO_EMAIL_SUFFIXES)


@router.get("/workspace/context", response_model=ContextOut)
def context(principal: PrincipalDep, session: SessionDep) -> ContextOut:
    user = session.get(User, principal.user_id)
    assert user is not None
    by_org: dict[str, list[str]] = {}
    for org_id, role in principal.roles:
        by_org.setdefault(org_id, []).append(role.value)
    orgs = session.scalars(select(Organization).where(Organization.id.in_(by_org))).all()
    return ContextOut(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        kind=user.kind.value,
        demo_account=_is_demo(user.email),
        organizations=[
            OrgOut(id=o.id, name=o.name, kind=o.kind.value, roles=sorted(by_org[o.id]))
            for o in sorted(orgs, key=lambda o: o.name)
        ],
    )


@router.get("/workspace/merchants", response_model=list[MerchantOut])
def merchants(
    principal: PrincipalDep, session: SessionDep, payments: PaymentServiceDep
) -> list[MerchantOut]:
    """Merchant accounts a buyer can contract with in the active payment environment.
    Exposes no PayPal identifiers."""
    rows = session.execute(
        select(MerchantAccount, Organization)
        .join(Organization, Organization.id == MerchantAccount.organization_id)
        .where(MerchantAccount.environment == payments.environment)
        .order_by(MerchantAccount.display_name)
    ).all()
    return [
        MerchantOut(
            id=m.id,
            display_name=m.display_name,
            organization_id=o.id,
            organization_name=o.name,
            environment=m.environment.value,
        )
        for m, o in rows
    ]


# ---------------------------------------------------------------------- overview


class RunSummary(BaseModel):
    id: str
    status: str
    verdict: str | None
    created_at: datetime
    finished_at: datetime | None
    counts: dict[str, int]
    awaiting_review: int


class PaymentSummary(BaseModel):
    order_id: str
    environment: str
    order_status: str
    paypal_order_status: str | None
    authorization_id: str | None
    authorization_status: str | None
    authorization_expires_at: datetime | None
    capture_status: str | None
    last_policy_decision: str | None
    last_policy_reasons: list[str]
    last_policy_evaluated_at: datetime | None
    void_status: str | None = None
    refunded: str | None = None  # completed refunds (PayPal-confirmed), decimal string


class AgreementSummary(BaseModel):
    id: str
    title: str
    status: str
    perspective: str  # buyer | provider
    buyer_organization_name: str
    merchant_display_name: str
    amount: str
    currency: str
    version_number: int
    version_status: str
    content_hash: str | None
    locked: bool
    created_at: datetime
    latest_run: RunSummary | None
    latest_completed_run: RunSummary | None
    payment: PaymentSummary | None
    open_resolution: dict[str, str] | None = None  # {"id", "status"} of the live case


def _run_summary(session: Session, run: VerificationRun | None) -> RunSummary | None:
    if run is None:
        return None
    counts: dict[str, int] = {}
    for r in run.results:
        counts[r.outcome.value] = counts.get(r.outcome.value, 0) + 1
    reviewed = set(
        session.scalars(
            select(ManualReviewDecision.criterion_id).where(
                ManualReviewDecision.verification_run_id == run.id
            )
        )
    )
    awaiting = sum(
        1
        for r in run.results
        if r.outcome.value == "manual_review_required" and r.criterion_id not in reviewed
    )
    return RunSummary(
        id=run.id,
        status=run.status.value,
        verdict=run.verdict.value if run.verdict else None,
        created_at=run.created_at,
        finished_at=run.finished_at,
        counts=counts,
        awaiting_review=awaiting,
    )


def _latest_order(session: Session, version_ids: list[str]) -> PaymentOrder | None:
    if not version_ids:
        return None
    return session.scalar(
        select(PaymentOrder)
        .where(PaymentOrder.agreement_version_id.in_(version_ids))
        .order_by(PaymentOrder.created_at.desc())
        .limit(1)
    )


def _payment_summary(session: Session, order: PaymentOrder | None) -> PaymentSummary | None:
    if order is None:
        return None
    auth = order.authorization
    last = None
    if auth is not None:
        last = session.scalar(
            select(PolicyDecisionRecord)
            .where(PolicyDecisionRecord.payment_authorization_id == auth.id)
            .order_by(PolicyDecisionRecord.evaluated_at.desc())
            .limit(1)
        )
    void = (
        session.scalar(
            select(AuthorizationVoid).where(AuthorizationVoid.payment_authorization_id == auth.id)
        )
        if auth
        else None
    )
    cap = auth.capture if auth else None
    refunded = (
        sum(r.amount_minor for r in cap.refunds if r.status.value == "completed") if cap else 0
    )
    return PaymentSummary(
        void_status=void.status.value if void else None,
        refunded=Money(refunded, cap.currency).to_decimal_str() if cap and refunded else None,
        order_id=order.id,
        environment=order.environment.value,
        order_status=order.status.value,
        paypal_order_status=order.paypal_status,
        authorization_id=auth.id if auth else None,
        authorization_status=auth.status.value if auth else None,
        authorization_expires_at=auth.expires_at if auth else None,
        capture_status=auth.capture.status.value if auth and auth.capture else None,
        last_policy_decision=last.decision.value if last else None,
        last_policy_reasons=[r.get("code", "") for r in last.reasons] if last else [],
        last_policy_evaluated_at=last.evaluated_at if last else None,
    )


def _runs(session: Session, version_ids: list[str]) -> list[VerificationRun]:
    if not version_ids:
        return []
    return list(
        session.scalars(
            select(VerificationRun)
            .where(VerificationRun.agreement_version_id.in_(version_ids))
            .order_by(VerificationRun.created_at.desc())
        )
    )


def _summary(session: Session, principal, a: Agreement) -> AgreementSummary:
    current = next(
        (v for v in a.versions if v.id == a.current_locked_version_id),
        max(a.versions, key=lambda v: v.version_number),
    )
    merchant = session.get(MerchantAccount, a.merchant_account_id)
    buyer = session.get(Organization, a.buyer_organization_id)
    assert merchant is not None and buyer is not None
    version_ids = [v.id for v in a.versions]
    runs = _runs(session, version_ids)
    completed = next((r for r in runs if r.status.value == "completed"), None)
    return AgreementSummary(
        id=a.id,
        title=current.title,
        status=a.status.value,
        perspective="buyer" if principal.member_of(a.buyer_organization_id) else "provider",
        buyer_organization_name=buyer.name,
        merchant_display_name=merchant.display_name,
        amount=Money(current.amount_minor, current.currency).to_decimal_str(),
        currency=current.currency,
        version_number=current.version_number,
        version_status=current.status.value,
        content_hash=current.content_hash,
        locked=a.current_locked_version_id is not None,
        created_at=a.created_at,
        latest_run=_run_summary(session, runs[0] if runs else None),
        latest_completed_run=_run_summary(session, completed),
        payment=_payment_summary(session, _latest_order(session, version_ids)),
        open_resolution=_live_case(session, a.id),
    )


def _live_case(session: Session, agreement_id: str) -> dict[str, str] | None:
    c = session.scalar(
        select(ResolutionCase)
        .where(
            ResolutionCase.agreement_id == agreement_id,
            ResolutionCase.status.not_in(["resolved", "closed_no_action", "rejected", "cancelled"]),
        )
        .order_by(ResolutionCase.created_at.desc())
        .limit(1)
    )
    return {"id": c.id, "status": c.status.value} if c else None


@router.get("/workspace/agreements", response_model=list[AgreementSummary])
def overview(
    principal: PrincipalDep, svc: AgreementServiceDep, session: SessionDep
) -> list[AgreementSummary]:
    return [_summary(session, principal, a) for a in svc.list_visible(principal)]


@router.get("/workspace/agreements/{agreement_id}", response_model=AgreementSummary)
def overview_one(
    agreement_id: str, principal: PrincipalDep, svc: AgreementServiceDep, session: SessionDep
) -> AgreementSummary:
    return _summary(session, principal, svc.get(principal, agreement_id))


# -------------------------------------------------------------------- payments


@router.get("/agreements/{agreement_id}/payments", response_model=list[OrderOut])
def agreement_payments(
    agreement_id: str,
    principal: PrincipalDep,
    svc: AgreementServiceDep,
    payments: PaymentServiceDep,
) -> list[OrderOut]:
    """Stored state only (no PayPal call); `GET /payments/orders/{id}` re-reads PayPal."""
    agreement = svc.get(principal, agreement_id)
    ids = [v.id for v in agreement.versions]
    orders = payments.session.scalars(
        select(PaymentOrder)
        .where(PaymentOrder.agreement_version_id.in_(ids))
        .order_by(PaymentOrder.created_at.desc())
    ).all()
    out = []
    for o in orders:
        cap = o.authorization.capture if o.authorization else None
        out.append(OrderOut.of(o, payments.refundable_balance(cap) if cap else None))
    return out


class CaptureApprovalOut(BaseModel):
    id: str
    inputs_sha256: str
    policy_decision_id: str
    approver_user_id: str
    approver_email: str
    approver_role: str
    approved_at: datetime
    consumed_by_capture_id: str | None


def _readable_auth(session: Session, svc, principal, auth_id: str) -> PaymentAuthorization:
    auth = session.get(PaymentAuthorization, auth_id)
    version_id = auth.order.agreement_version_id if auth else None
    if auth is None or version_id is None:
        raise GovernanceError("NOT_FOUND", "authorization not found", http_status=404)
    version = session.get(AgreementVersion, version_id)
    assert version is not None
    svc.get(principal, version.agreement_id)
    return auth


@router.get(
    "/payments/authorizations/{auth_id}/capture-approvals",
    response_model=list[CaptureApprovalOut],
)
def capture_approvals(
    auth_id: str, principal: PrincipalDep, svc: AgreementServiceDep, session: SessionDep
) -> list[CaptureApprovalOut]:
    _readable_auth(session, svc, principal, auth_id)
    rows = session.execute(
        select(CaptureApproval, User.email)
        .join(User, User.id == CaptureApproval.approver_user_id)
        .where(CaptureApproval.payment_authorization_id == auth_id)
        .order_by(CaptureApproval.approved_at.desc())
    ).all()
    return [
        CaptureApprovalOut(
            id=a.id,
            inputs_sha256=a.inputs_sha256,
            policy_decision_id=a.policy_decision_id,
            approver_user_id=a.approver_user_id,
            approver_email=email,
            approver_role=a.approver_role,
            approved_at=a.approved_at,
            consumed_by_capture_id=a.consumed_by_capture_id,
        )
        for a, email in rows
    ]


# ----------------------------------------------------------------------- ledger


class LedgerRow(BaseModel):
    seq: int
    occurred_at: datetime
    event_type: str
    category: str
    entity_type: str
    entity_id: str
    agreement_id: str | None
    agreement_title: str | None
    agreement_version: int | None
    actor: str
    actor_type: str  # human_account | service_account | system
    demo_account: bool
    provenance: str | None  # "scripted" only when a persisted marker says so
    resolution_case_id: str | None
    verification_run_id: str | None
    criterion_key: str | None
    policy_decision: str | None
    policy_reasons: list[str]
    payment_status: str | None
    payment_environment: str | None  # sandbox | simulated (rehearsal) for payment rows
    amount: str | None
    currency: str | None
    evidence_ref: str | None
    transaction_ref: str | None
    data: dict[str, Any]
    hash: str
    prev_hash: str


class LedgerPage(BaseModel):
    total: int
    offset: int
    limit: int
    rows: list[LedgerRow]
    chain_note: str


CHAIN_NOTE = (
    "Rows come from the append-only audit log. Each event stores sha256(previous hash + "
    "event). This makes edits made outside the application detectable; it is not "
    "tamper-proof against someone with database write access."
)


class _Ref:
    """What an audit entity id belongs to, and the facts the ledger shows for it."""

    def __init__(self, agreement: Agreement, **facts: Any) -> None:
        self.agreement = agreement
        self.facts = facts


def _index(session: Session, agreements: list[Agreement]) -> dict[str, _Ref]:
    idx: dict[str, _Ref] = {}
    by_version: dict[str, tuple[Agreement, AgreementVersion]] = {}
    for a in agreements:
        idx[a.id] = _Ref(a)
        for v in a.versions:
            by_version[v.id] = (a, v)
            idx[v.id] = _Ref(a, version=v.version_number)
    vids = list(by_version)
    if not vids:
        return idx

    def ver(vid: str) -> tuple[Agreement, int]:
        a, v = by_version[vid]
        return a, v.version_number

    for d in session.scalars(
        select(AgreementDraft).where(
            AgreementDraft.accepted_agreement_id.in_([a.id for a in agreements])
        )
    ):
        a = next(x for x in agreements if x.id == d.accepted_agreement_id)
        idx[d.id] = _Ref(a)
    for s in session.scalars(
        select(EvidenceSubmission).where(EvidenceSubmission.agreement_version_id.in_(vids))
    ):
        a, n = ver(s.agreement_version_id)
        idx[s.id] = _Ref(a, version=n, evidence_ref=s.submission_hash)
    runs = list(
        session.scalars(
            select(VerificationRun).where(VerificationRun.agreement_version_id.in_(vids))
        )
    )
    run_ver = {r.id: r.agreement_version_id for r in runs}
    for r in runs:
        a, n = ver(r.agreement_version_id)
        idx[r.id] = _Ref(a, version=n, run=r.id)
    rids = list(run_ver)
    if rids:
        for an in session.scalars(
            select(EvidenceAnalysis).where(EvidenceAnalysis.run_id.in_(rids))
        ):
            a, n = ver(run_ver[an.run_id])
            idx[an.id] = _Ref(a, version=n, run=an.run_id)
        for m, key in session.execute(
            select(ManualReviewDecision, AcceptanceCriterion.key)
            .join(AcceptanceCriterion, AcceptanceCriterion.id == ManualReviewDecision.criterion_id)
            .where(ManualReviewDecision.verification_run_id.in_(rids))
        ).all():
            a, n = ver(m.agreement_version_id)
            idx[m.id] = _Ref(
                a,
                version=n,
                run=m.verification_run_id,
                criterion=key,
                evidence_ref=m.evidence_snapshot_sha256,
                scripted=m.rationale.startswith(SCRIPTED_MARKER),
            )
    orders = list(
        session.scalars(select(PaymentOrder).where(PaymentOrder.agreement_version_id.in_(vids)))
    )
    for o in orders:
        assert o.agreement_version_id is not None
        a, n = ver(o.agreement_version_id)
        money = Money(o.amount_minor, o.currency)
        idx[o.id] = _Ref(
            a,
            version=n,
            payment_status=f"order {o.status.value}",
            amount=money.to_decimal_str(),
            currency=o.currency,
            txn=o.paypal_order_id,
            env=o.environment.value,
        )
        auth = o.authorization
        if auth is None:
            continue
        idx[auth.id] = _Ref(
            a,
            version=n,
            payment_status=f"authorization {auth.status.value}",
            amount=Money(auth.amount_minor, auth.currency).to_decimal_str(),
            currency=auth.currency,
            txn=auth.paypal_authorization_id,
            env=o.environment.value,
        )
        for p in session.scalars(
            select(PolicyDecisionRecord).where(
                PolicyDecisionRecord.payment_authorization_id == auth.id
            )
        ):
            idx[p.id] = _Ref(
                a,
                version=n,
                run=p.verification_run_id,
                policy=p.decision.value,
                reasons=[r.get("code", "") for r in p.reasons],
                evidence_ref=p.inputs.get("evidence_snapshot_sha256"),
            )
        for ca in session.scalars(
            select(CaptureApproval).where(CaptureApproval.payment_authorization_id == auth.id)
        ):
            idx[ca.id] = _Ref(a, version=n, policy_inputs=ca.inputs_sha256, env=o.environment.value)
        cap: PaymentCapture | None = auth.capture
        if cap is not None:
            idx[cap.id] = _Ref(
                a,
                version=n,
                run=cap.verification_run_id,
                payment_status=f"capture {cap.status.value}",
                amount=Money(cap.amount_minor, cap.currency).to_decimal_str(),
                currency=cap.currency,
                txn=cap.paypal_capture_id,
                env=o.environment.value,
            )
            for rf in session.scalars(
                select(PaymentRefund).where(PaymentRefund.payment_capture_id == cap.id)
            ):
                idx[rf.id] = _Ref(
                    a,
                    version=n,
                    payment_status=f"refund {rf.status.value}",
                    amount=Money(rf.amount_minor, rf.currency).to_decimal_str(),
                    currency=rf.currency,
                    txn=rf.paypal_refund_id,
                    env=o.environment.value,
                )
    # P08: resolution cases, money-policy decisions and voids.
    by_id = {a.id: a for a in agreements}
    for rc in session.scalars(
        select(ResolutionCase).where(ResolutionCase.agreement_id.in_(list(by_id)))
    ):
        a = by_id[rc.agreement_id]
        n = next(v.version_number for v in a.versions if v.id == rc.agreement_version_id)
        cap = session.get(PaymentCapture, rc.payment_capture_id) if rc.payment_capture_id else None
        idx[rc.id] = _Ref(
            a,
            version=n,
            case=rc.id,
            evidence_ref=rc.evidence_snapshot_sha256,
            amount=Money(rc.requested_amount_minor, rc.currency).to_decimal_str()
            if rc.requested_amount_minor
            else None,
            currency=rc.currency,
            txn=cap.paypal_capture_id if cap else None,
            env=(rc.evidence_snapshot.get("payment") or {}).get("environment"),
        )
        for fd in session.scalars(
            select(FinancialPolicyDecision).where(FinancialPolicyDecision.case_id == rc.id)
        ):
            idx[fd.id] = _Ref(
                a,
                version=n,
                case=rc.id,
                policy=fd.decision.value,
                reasons=[r.get("code", "") for r in fd.reasons],
            )
    auth_ref = {
        k: v
        for k, v in idx.items()
        if v.facts.get("payment_status", "").startswith("authorization")
    }
    if auth_ref:
        for fd in session.scalars(
            select(FinancialPolicyDecision).where(
                FinancialPolicyDecision.payment_authorization_id.in_(list(auth_ref))
            )
        ):
            r0 = auth_ref[fd.payment_authorization_id or ""]
            idx[fd.id] = _Ref(
                r0.agreement,
                version=r0.facts.get("version"),
                policy=fd.decision.value,
                reasons=[r.get("code", "") for r in fd.reasons],
                env=r0.facts.get("env"),
            )
        for vo in session.scalars(
            select(AuthorizationVoid).where(
                AuthorizationVoid.payment_authorization_id.in_(list(auth_ref))
            )
        ):
            r0 = auth_ref[vo.payment_authorization_id]
            idx[vo.id] = _Ref(
                r0.agreement,
                version=r0.facts.get("version"),
                case=vo.case_id,
                payment_status=f"void {vo.status.value}",
                amount=r0.facts.get("amount"),
                currency=r0.facts.get("currency"),
                txn=r0.facts.get("txn"),
                env=r0.facts.get("env"),
            )
    return idx


# audit action prefix -> ledger category
_PREFIX = {
    "agreement": "agreement",
    "ai": "ai",
    "evidence": "verification",
    "verification": "verification",
    "manual_review": "review",
    "policy": "policy",
    "capture": "approval",
    "payment": "payment",
    "resolution": "resolution",
}


def _category(action: str) -> str:
    return _PREFIX.get(action.split(".", 1)[0], "other")


@router.get("/workspace/ledger", response_model=LedgerPage)
def ledger(
    principal: PrincipalDep,
    svc: AgreementServiceDep,
    session: SessionDep,
    agreement_id: str | None = None,
    category: str | None = Query(default=None, pattern=r"^[a-z]{2,20}$"),
    q: str | None = Query(default=None, max_length=100),
    include_previews: bool = False,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> LedgerPage:
    agreements = [svc.get(principal, agreement_id)] if agreement_id else svc.list_visible(principal)
    idx = _index(session, agreements)
    if not idx:
        return LedgerPage(total=0, offset=offset, limit=limit, rows=[], chain_note=CHAIN_NOTE)
    stmt = select(AuditEvent).where(AuditEvent.entity_id.in_(list(idx)))
    if not include_previews:
        stmt = stmt.where(~AuditEvent.action.like("policy.preview.%"))
    if category:
        prefixes = [p for p, c in _PREFIX.items() if c == category]
        stmt = stmt.where(or_(false(), *[AuditEvent.action.like(f"{p}.%") for p in prefixes]))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                AuditEvent.action.ilike(like),
                AuditEvent.actor.ilike(like),
                AuditEvent.entity_id.ilike(like),
            )
        )
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page = list(session.scalars(stmt.order_by(AuditEvent.seq.desc()).offset(offset).limit(limit)))
    emails = {e.actor[5:] for e in page if e.actor.startswith("user:")}
    users = {u.email: u for u in session.scalars(select(User).where(User.email.in_(emails)))}
    rows = []
    for e in page:
        ref = idx[e.entity_id]
        f = ref.facts
        email = e.actor[5:] if e.actor.startswith("user:") else None
        user = users.get(email) if email else None
        if user is None:
            actor_type = "system"
        else:
            actor_type = "service_account" if user.kind is UserKind.SERVICE else "human_account"
        cur = next(
            (v for v in ref.agreement.versions if v.version_number == f.get("version")),
            max(ref.agreement.versions, key=lambda v: v.version_number),
        )
        rows.append(
            LedgerRow(
                seq=e.seq,
                occurred_at=e.occurred_at,
                event_type=e.action,
                category=_category(e.action),
                entity_type=e.entity_type,
                entity_id=e.entity_id,
                agreement_id=ref.agreement.id,
                agreement_title=cur.title,
                agreement_version=f.get("version"),
                actor=e.actor,
                actor_type=actor_type,
                demo_account=bool(email and _is_demo(email)),
                provenance="scripted" if f.get("scripted") else None,
                resolution_case_id=f.get("case"),
                verification_run_id=f.get("run"),
                criterion_key=f.get("criterion"),
                policy_decision=f.get("policy"),
                policy_reasons=f.get("reasons", []),
                payment_status=f.get("payment_status"),
                payment_environment=f.get("env"),
                amount=f.get("amount"),
                currency=f.get("currency"),
                evidence_ref=f.get("evidence_ref"),
                transaction_ref=f.get("txn"),
                data=e.data,
                hash=e.hash,
                prev_hash=e.prev_hash,
            )
        )
    return LedgerPage(total=total, offset=offset, limit=limit, rows=rows, chain_note=CHAIN_NOTE)
