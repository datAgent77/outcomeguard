"""Agreement domain service: drafting, criteria, proposal, buyer approval and locking.

Authorization (who), state (when) and concurrency (which revision) are checked on every
mutation, in that order, inside a transaction that holds a row lock on the agreement.
Every material change writes an audit event. No AI and no payment state lives here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from outcomeguard.auth import Forbidden, Principal
from outcomeguard.domain.criteria import CriterionInput, landing_page_template, method_for
from outcomeguard.models import (
    AcceptanceCriterion,
    Agreement,
    AgreementStatus,
    AgreementVersion,
    Approval,
    ApprovalDecision,
    ApprovalSubject,
    CriterionSource,
    MerchantAccount,
    Organization,
    OrgKind,
    Role,
    ServiceCategory,
    VersionStatus,
)
from outcomeguard.models.agreements import OPEN_VERSION_STATUSES
from outcomeguard.models.audit import record_audit
from outcomeguard.models.base import utcnow
from outcomeguard.money import Money

EDIT_ROLES = (Role.BUYER_ADMIN,)
APPROVE_ROLES = (Role.BUYER_APPROVER, Role.BUYER_ADMIN)
MERCHANT_READ_ROLES = (Role.MERCHANT_ADMIN, Role.MERCHANT_APPROVER, Role.PROVIDER)

EDITABLE_FIELDS = (
    "title",
    "description",
    "requirements_text",
    "amount_minor",
    "currency",
    "delivery_deadline",
)


class AgreementError(Exception):
    def __init__(self, code: str, message: str, *, http_status: int = 409) -> None:
        self.code = code
        self.message = message
        self.http_status = http_status
        super().__init__(f"{code}: {message}")


@dataclass
class DraftContent:
    title: str
    description: str
    requirements_text: str
    amount: Money
    delivery_deadline: datetime


@dataclass
class AgreementService:
    session: Session

    # ------------------------------------------------------------------ reads

    def get(self, principal: Principal, agreement_id: str) -> Agreement:
        agreement = self.session.get(Agreement, agreement_id)
        if agreement is None or not self._can_read(principal, agreement):
            raise AgreementError("NOT_FOUND", "agreement not found", http_status=404)
        return agreement

    def list_visible(self, principal: Principal) -> list[Agreement]:
        org_ids = {o for o, _ in principal.roles}
        if not org_ids:
            return []
        merchant_ids = select(MerchantAccount.id).where(
            MerchantAccount.organization_id.in_(org_ids)
        )
        stmt = (
            select(Agreement)
            .where(
                or_(
                    Agreement.buyer_organization_id.in_(org_ids),
                    Agreement.merchant_account_id.in_(merchant_ids),
                )
            )
            .order_by(Agreement.created_at.desc())
        )
        return [a for a in self.session.scalars(stmt) if self._can_read(principal, a)]

    def get_version(
        self, principal: Principal, agreement_id: str, version_number: int
    ) -> AgreementVersion:
        agreement = self.get(principal, agreement_id)
        for v in agreement.versions:
            if v.version_number == version_number:
                return v
        raise AgreementError("NOT_FOUND", "version not found", http_status=404)

    def _can_read(self, principal: Principal, agreement: Agreement) -> bool:
        if principal.member_of(agreement.buyer_organization_id):
            return True
        merchant = self.session.get(MerchantAccount, agreement.merchant_account_id)
        return bool(merchant) and principal.has(merchant.organization_id, *MERCHANT_READ_ROLES)

    # ------------------------------------------------------------- mutations

    def create(
        self,
        principal: Principal,
        *,
        buyer_organization_id: str,
        merchant_account_id: str,
        content: DraftContent,
        criteria: list[CriterionInput] | None = None,
        use_template: bool = False,
        template_min_performance: int = 90,
        criteria_source: CriterionSource | None = None,
    ) -> Agreement:
        principal.require(buyer_organization_id, *EDIT_ROLES, action="creating an agreement")
        org = self.session.get(Organization, buyer_organization_id)
        if org is None or org.kind is not OrgKind.BUYER:
            raise AgreementError("INVALID_BUYER", "buyer organization not found", http_status=422)
        merchant = self.session.get(MerchantAccount, merchant_account_id)
        if merchant is None:
            raise AgreementError("INVALID_MERCHANT", "merchant not found", http_status=422)
        if merchant.organization_id == buyer_organization_id:
            raise AgreementError(
                "INVALID_MERCHANT", "buyer cannot contract itself", http_status=422
            )
        self._validate_content(content)

        if criteria is None:
            criteria = (
                landing_page_template(min_performance_score=template_min_performance)
                if use_template
                else []
            )
        source = criteria_source or (
            CriterionSource.TEMPLATE if use_template else CriterionSource.BUYER
        )

        agreement = Agreement(
            buyer_organization_id=buyer_organization_id,
            merchant_account_id=merchant_account_id,
            service_category=ServiceCategory.LANDING_PAGE,
            status=AgreementStatus.DRAFT,
            created_by_user_id=principal.user_id,
        )
        self.session.add(agreement)
        self.session.flush()
        version = AgreementVersion(
            agreement_id=agreement.id,
            version_number=1,
            status=VersionStatus.DRAFT,
            title=content.title,
            description=content.description,
            requirements_text=content.requirements_text,
            amount_minor=content.amount.minor,
            currency=content.amount.currency,
            delivery_deadline=content.delivery_deadline,
            created_by_user_id=principal.user_id,
        )
        self.session.add(version)
        self.session.flush()
        self._write_criteria(version, criteria, source)
        record_audit(
            self.session,
            actor=principal.label,
            action="agreement.created",
            entity_type="agreement",
            entity_id=agreement.id,
            data={
                "version": 1,
                "amount": str(content.amount),
                "criteria": len(criteria),
                "template": use_template,
            },
        )
        self.session.commit()
        return agreement

    def update_draft(
        self,
        principal: Principal,
        agreement_id: str,
        version_number: int,
        *,
        expected_revision: int,
        changes: dict[str, Any],
    ) -> AgreementVersion:
        agreement, version = self._lock_for_edit(
            principal, agreement_id, version_number, expected_revision
        )
        self._require_status(version, VersionStatus.DRAFT, "edit")
        unknown = set(changes) - set(EDITABLE_FIELDS)
        if unknown:
            raise AgreementError(
                "INVALID_FIELDS", f"not editable: {sorted(unknown)}", http_status=422
            )
        before = {k: _jsonable(getattr(version, k)) for k in changes}
        for k, v in changes.items():
            setattr(version, k, v)
        self._validate_content(
            DraftContent(
                title=version.title,
                description=version.description,
                requirements_text=version.requirements_text,
                amount=Money(version.amount_minor, version.currency),
                delivery_deadline=version.delivery_deadline,
            )
        )
        self.session.flush()
        record_audit(
            self.session,
            actor=principal.label,
            action="agreement.version.updated",
            entity_type="agreement_version",
            entity_id=version.id,
            data={"before": before, "after": {k: _jsonable(getattr(version, k)) for k in changes}},
        )
        self.session.commit()
        return version

    def replace_criteria(
        self,
        principal: Principal,
        agreement_id: str,
        version_number: int,
        *,
        expected_revision: int,
        criteria: list[CriterionInput],
    ) -> AgreementVersion:
        _, version = self._lock_for_edit(principal, agreement_id, version_number, expected_revision)
        self._require_status(version, VersionStatus.DRAFT, "change criteria of")
        before = [c.key for c in version.criteria]
        for c in list(version.criteria):
            self.session.delete(c)
        self.session.flush()
        version.criteria = []
        self._write_criteria(version, criteria, CriterionSource.BUYER)
        version.updated_at = utcnow()  # bumps revision (optimistic lock)
        self.session.flush()
        record_audit(
            self.session,
            actor=principal.label,
            action="agreement.criteria.replaced",
            entity_type="agreement_version",
            entity_id=version.id,
            data={"before": before, "after": [c.key for c in criteria]},
        )
        self.session.commit()
        return version

    def propose(
        self,
        principal: Principal,
        agreement_id: str,
        version_number: int,
        *,
        expected_revision: int,
    ) -> AgreementVersion:
        _, version = self._lock_for_edit(principal, agreement_id, version_number, expected_revision)
        self._require_status(version, VersionStatus.DRAFT, "propose")
        if not version.criteria:
            raise AgreementError(
                "NO_CRITERIA", "add at least one acceptance criterion", http_status=422
            )
        if version.delivery_deadline <= utcnow():
            raise AgreementError(
                "DEADLINE_PASSED", "delivery deadline is in the past", http_status=422
            )
        version.status = VersionStatus.PROPOSED
        version.proposed_at = utcnow()
        version.content_hash = content_hash(version)
        self.session.flush()
        record_audit(
            self.session,
            actor=principal.label,
            action="agreement.version.proposed",
            entity_type="agreement_version",
            entity_id=version.id,
            data={"content_hash": version.content_hash},
        )
        self.session.commit()
        return version

    def withdraw(
        self,
        principal: Principal,
        agreement_id: str,
        version_number: int,
        *,
        expected_revision: int,
    ) -> AgreementVersion:
        _, version = self._lock_for_edit(principal, agreement_id, version_number, expected_revision)
        self._require_status(version, VersionStatus.PROPOSED, "withdraw")
        old_hash = version.content_hash
        version.status = VersionStatus.DRAFT
        version.content_hash = None
        version.proposed_at = None
        self.session.flush()
        record_audit(
            self.session,
            actor=principal.label,
            action="agreement.version.withdrawn",
            entity_type="agreement_version",
            entity_id=version.id,
            data={"withdrawn_hash": old_hash},
        )
        self.session.commit()
        return version

    def approve(
        self,
        principal: Principal,
        agreement_id: str,
        version_number: int,
        *,
        content_hash_seen: str,
        note: str | None = None,
    ) -> AgreementVersion:
        """Buyer approval of a proposed version. Approval locks it."""
        agreement = self._lock_agreement(principal, agreement_id)
        self._require(principal, agreement, APPROVE_ROLES, "approving an agreement")
        version = self._version_of(agreement, version_number)
        if version.status in (VersionStatus.LOCKED, VersionStatus.SUPERSEDED):
            raise AgreementError("ALREADY_APPROVED", "this version is already approved and locked")
        self._require_status(version, VersionStatus.PROPOSED, "approve")
        recomputed = content_hash(version)
        if recomputed != version.content_hash:
            # Should be impossible (content is frozen while proposed); refuse loudly.
            raise AgreementError(
                "INTEGRITY_ERROR", "proposed content changed after proposal", http_status=500
            )
        if content_hash_seen != version.content_hash:
            raise AgreementError(
                "CONTENT_CHANGED",
                "the version you reviewed differs from the current proposal; reload and review again",
            )
        approval = Approval(
            subject_type=ApprovalSubject.AGREEMENT_VERSION,
            subject_id=version.id,
            decision=ApprovalDecision.APPROVED,
            content_hash=version.content_hash,
            approver_user_id=principal.user_id,
            approver_role=_approving_role(principal, agreement.buyer_organization_id).value,
            organization_id=agreement.buyer_organization_id,
            note=note,
        )
        self.session.add(approval)
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            raise AgreementError("ALREADY_APPROVED", "this version was already approved") from exc

        previous = (
            self.session.get(AgreementVersion, agreement.current_locked_version_id)
            if agreement.current_locked_version_id
            else None
        )
        if previous is not None:
            previous.status = VersionStatus.SUPERSEDED
        version.status = VersionStatus.LOCKED
        version.locked_at = utcnow()
        agreement.current_locked_version_id = version.id
        agreement.status = AgreementStatus.ACTIVE
        self.session.flush()
        record_audit(
            self.session,
            actor=principal.label,
            action="agreement.version.approved_and_locked",
            entity_type="agreement_version",
            entity_id=version.id,
            data={
                "approval_id": approval.id,
                "content_hash": version.content_hash,
                "supersedes": previous.id if previous else None,
            },
        )
        self.session.commit()
        return version

    def new_version(self, principal: Principal, agreement_id: str) -> AgreementVersion:
        """Start a draft amendment from the currently locked version."""
        agreement = self._lock_agreement(principal, agreement_id)
        self._require(principal, agreement, EDIT_ROLES, "amending an agreement")
        if any(v.status in OPEN_VERSION_STATUSES for v in agreement.versions):
            raise AgreementError("OPEN_VERSION_EXISTS", "finish or discard the open draft first")
        if agreement.current_locked_version_id is None:
            raise AgreementError("NOT_LOCKED", "amend only after a version is locked")
        base = self.session.get(AgreementVersion, agreement.current_locked_version_id)
        assert base is not None
        number = max(v.version_number for v in agreement.versions) + 1
        draft = AgreementVersion(
            agreement_id=agreement.id,
            version_number=number,
            status=VersionStatus.DRAFT,
            supersedes_version_id=base.id,
            title=base.title,
            description=base.description,
            requirements_text=base.requirements_text,
            amount_minor=base.amount_minor,
            currency=base.currency,
            delivery_deadline=base.delivery_deadline,
            created_by_user_id=principal.user_id,
        )
        self.session.add(draft)
        self.session.flush()
        for c in base.criteria:
            self.session.add(
                AcceptanceCriterion(
                    agreement_version_id=draft.id,
                    position=c.position,
                    key=c.key,
                    title=c.title,
                    kind=c.kind,
                    verification_method=c.verification_method,
                    blocking=c.blocking,
                    source=c.source,
                    spec=c.spec,
                )
            )
        self.session.flush()
        record_audit(
            self.session,
            actor=principal.label,
            action="agreement.version.created",
            entity_type="agreement_version",
            entity_id=draft.id,
            data={"version": number, "from_version": base.version_number},
        )
        self.session.commit()
        return draft

    def discard(
        self,
        principal: Principal,
        agreement_id: str,
        version_number: int,
        *,
        expected_revision: int,
    ) -> AgreementVersion:
        _, version = self._lock_for_edit(principal, agreement_id, version_number, expected_revision)
        if version.status not in OPEN_VERSION_STATUSES:
            raise AgreementError(
                "INVALID_TRANSITION", f"cannot discard a {version.status.value} version"
            )
        version.status = VersionStatus.DISCARDED
        self.session.flush()
        record_audit(
            self.session,
            actor=principal.label,
            action="agreement.version.discarded",
            entity_type="agreement_version",
            entity_id=version.id,
            data={},
        )
        self.session.commit()
        return version

    # --------------------------------------------------------------- helpers

    def _lock_agreement(self, principal: Principal, agreement_id: str) -> Agreement:
        self.session.expire_all()
        agreement = self.session.scalar(
            select(Agreement).where(Agreement.id == agreement_id).with_for_update()
        )
        if agreement is None or not self._can_read(principal, agreement):
            self.session.rollback()
            raise AgreementError("NOT_FOUND", "agreement not found", http_status=404)
        return agreement

    def _lock_for_edit(
        self, principal: Principal, agreement_id: str, version_number: int, expected_revision: int
    ) -> tuple[Agreement, AgreementVersion]:
        agreement = self._lock_agreement(principal, agreement_id)
        self._require(principal, agreement, EDIT_ROLES, "editing an agreement")
        version = self._version_of(agreement, version_number)
        if version.revision != expected_revision:
            self.session.rollback()
            raise AgreementError(
                "STALE_REVISION",
                f"version is at revision {version.revision}, you edited {expected_revision}",
            )
        return agreement, version

    def _require(
        self, principal: Principal, agreement: Agreement, roles: tuple[Role, ...], action: str
    ) -> None:
        """Caller already passed the read check, so a refusal is an honest 403."""
        if not principal.has(agreement.buyer_organization_id, *roles):
            self.session.rollback()
            raise Forbidden(f"{action} requires role {' or '.join(r.value for r in roles)}")

    def _version_of(self, agreement: Agreement, number: int) -> AgreementVersion:
        for v in agreement.versions:
            if v.version_number == number:
                return v
        self.session.rollback()
        raise AgreementError("NOT_FOUND", "version not found", http_status=404)

    def _require_status(self, version: AgreementVersion, status: VersionStatus, verb: str) -> None:
        if version.status is not status:
            self.session.rollback()
            code = (
                "VERSION_LOCKED"
                if version.status in (VersionStatus.LOCKED, VersionStatus.SUPERSEDED)
                else "INVALID_TRANSITION"
            )
            raise AgreementError(code, f"cannot {verb} a {version.status.value} version")

    @staticmethod
    def _validate_content(c: DraftContent) -> None:
        if not c.title.strip():
            raise AgreementError("INVALID_CONTENT", "title is required", http_status=422)
        if c.amount.minor <= 0:
            raise AgreementError("INVALID_CONTENT", "amount must be positive", http_status=422)
        if c.delivery_deadline.tzinfo is None:
            raise AgreementError("INVALID_CONTENT", "deadline needs a timezone", http_status=422)

    def _write_criteria(
        self, version: AgreementVersion, criteria: list[CriterionInput], source: CriterionSource
    ) -> None:
        keys = [c.key for c in criteria]
        if len(keys) != len(set(keys)):
            raise AgreementError(
                "DUPLICATE_CRITERION", "criterion keys must be unique", http_status=422
            )
        if len(criteria) > 20:
            raise AgreementError("TOO_MANY_CRITERIA", "at most 20 criteria", http_status=422)
        for i, c in enumerate(criteria):
            self.session.add(
                AcceptanceCriterion(
                    agreement_version_id=version.id,
                    position=i,
                    key=c.key,
                    title=c.title,
                    kind=c.spec.kind,
                    verification_method=method_for(c.spec.kind),
                    blocking=c.blocking,
                    source=source,
                    spec=c.spec.model_dump(mode="json"),
                )
            )
        self.session.flush()
        self.session.refresh(version, ["criteria"])


def content_hash(version: AgreementVersion) -> str:
    """SHA-256 over the canonical content a buyer approves."""
    agreement = version.agreement
    body = {
        "agreement_id": agreement.id,
        "buyer_organization_id": agreement.buyer_organization_id,
        "merchant_account_id": agreement.merchant_account_id,
        "service_category": agreement.service_category.value,
        "version_number": version.version_number,
        "title": version.title,
        "description": version.description,
        "requirements_text": version.requirements_text,
        "amount_minor": version.amount_minor,
        "currency": version.currency,
        "delivery_deadline": version.delivery_deadline.astimezone(UTC).isoformat(),
        "criteria": [
            {
                "position": c.position,
                "key": c.key,
                "title": c.title,
                "kind": c.kind.value,
                "blocking": c.blocking,
                "spec": c.spec,
            }
            for c in sorted(version.criteria, key=lambda c: c.position)
        ],
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _approving_role(principal: Principal, org_id: str) -> Role:
    for r in APPROVE_ROLES:
        if principal.has(org_id, r):
            return r
    raise Forbidden("no approving role")


def _jsonable(v: Any) -> Any:
    return v.isoformat() if isinstance(v, datetime) else v
