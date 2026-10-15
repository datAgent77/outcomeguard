"""Agreements, their versions, acceptance criteria and human approvals.

An Agreement is a container; its content lives in AgreementVersions. A version moves

    draft ──propose──▶ proposed ──buyer approval──▶ locked ──(newer version locked)──▶ superseded
      ▲                   │
      └──── withdraw ─────┘          draft / proposed ──discard──▶ discarded

Content is editable only in `draft`. `propose` freezes it and records a SHA-256
`content_hash` of the canonical content (fields + criteria); the buyer's approval must
quote that hash, so what was approved is exactly what gets locked. Locked/superseded
versions are immutable: enforced by the service layer *and* by Postgres triggers
(see the P03 migration), so even a buggy code path cannot rewrite history.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from outcomeguard.domain.criteria import CriterionKind, VerificationMethod
from outcomeguard.models.base import Base, IdMixin, TimestampMixin, enum_col


class ServiceCategory(StrEnum):
    LANDING_PAGE = "landing_page"


class AgreementStatus(StrEnum):
    DRAFT = "draft"  # no version locked yet
    ACTIVE = "active"  # has a locked version
    CANCELLED = "cancelled"


class VersionStatus(StrEnum):
    DRAFT = "draft"
    PROPOSED = "proposed"
    LOCKED = "locked"
    SUPERSEDED = "superseded"
    DISCARDED = "discarded"


OPEN_VERSION_STATUSES = (VersionStatus.DRAFT, VersionStatus.PROPOSED)
FROZEN_VERSION_STATUSES = (VersionStatus.LOCKED, VersionStatus.SUPERSEDED)


class CriterionSource(StrEnum):
    TEMPLATE = "template"
    BUYER = "buyer"
    AI_PROPOSED = "ai_proposed"  # P05


class Agreement(IdMixin, TimestampMixin, Base):
    __tablename__ = "agreements"

    buyer_organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    merchant_account_id: Mapped[str] = mapped_column(ForeignKey("merchant_accounts.id"))
    service_category: Mapped[ServiceCategory] = mapped_column(
        enum_col(ServiceCategory, "service_category")
    )
    status: Mapped[AgreementStatus] = mapped_column(enum_col(AgreementStatus, "agreement_status"))
    current_locked_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("agreement_versions.id", use_alter=True, name="fk_agreements_locked_version")
    )
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))

    versions: Mapped[list[AgreementVersion]] = relationship(
        back_populates="agreement",
        foreign_keys="AgreementVersion.agreement_id",
        order_by="AgreementVersion.version_number",
    )


class AgreementVersion(IdMixin, TimestampMixin, Base):
    __tablename__ = "agreement_versions"
    __table_args__ = (
        UniqueConstraint("agreement_id", "version_number", name="uq_agreement_versions_number"),
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        # At most one open (draft/proposed) version per agreement.
        Index(
            "uq_agreement_versions_one_open",
            "agreement_id",
            unique=True,
            postgresql_where=text("status IN ('draft', 'proposed')"),
        ),
    )

    agreement_id: Mapped[str] = mapped_column(ForeignKey("agreements.id"))
    version_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[VersionStatus] = mapped_column(enum_col(VersionStatus, "version_status"))
    # Optimistic concurrency: clients send the revision they edited (If-Match semantics).
    revision: Mapped[int] = mapped_column(Integer, default=1)
    supersedes_version_id: Mapped[str | None] = mapped_column(ForeignKey("agreement_versions.id"))

    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    requirements_text: Mapped[str] = mapped_column(Text)  # buyer's natural-language promise
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    delivery_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    content_hash: Mapped[str | None] = mapped_column(String(64))
    proposed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))

    agreement: Mapped[Agreement] = relationship(
        back_populates="versions", foreign_keys=[agreement_id]
    )
    criteria: Mapped[list[AcceptanceCriterion]] = relationship(
        back_populates="version", order_by="AcceptanceCriterion.position"
    )

    __mapper_args__ = {"version_id_col": revision}


class AcceptanceCriterion(IdMixin, TimestampMixin, Base):
    __tablename__ = "acceptance_criteria"
    __table_args__ = (
        UniqueConstraint("agreement_version_id", "key", name="uq_acceptance_criteria_version_key"),
        UniqueConstraint(
            "agreement_version_id", "position", name="uq_acceptance_criteria_version_position"
        ),
    )

    agreement_version_id: Mapped[str] = mapped_column(ForeignKey("agreement_versions.id"))
    position: Mapped[int] = mapped_column(Integer)
    key: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[CriterionKind] = mapped_column(enum_col(CriterionKind, "criterion_kind"))
    verification_method: Mapped[VerificationMethod] = mapped_column(
        enum_col(VerificationMethod, "verification_method")
    )
    blocking: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[CriterionSource] = mapped_column(enum_col(CriterionSource, "criterion_source"))
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)

    version: Mapped[AgreementVersion] = relationship(back_populates="criteria")


class ApprovalSubject(StrEnum):
    AGREEMENT_VERSION = "agreement_version"
    CAPTURE = "capture"  # P06: human approval of a payment capture


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class Approval(IdMixin, Base):
    """An explicit human decision, bound to the exact content it was made on."""

    __tablename__ = "approvals"
    __table_args__ = (
        # One decision per subject per approving role.
        UniqueConstraint(
            "subject_type", "subject_id", "approver_role", name="uq_approvals_subject_role"
        ),
    )

    subject_type: Mapped[ApprovalSubject] = mapped_column(
        enum_col(ApprovalSubject, "approval_subject")
    )
    subject_id: Mapped[str] = mapped_column(String(36), index=True)
    decision: Mapped[ApprovalDecision] = mapped_column(
        enum_col(ApprovalDecision, "approval_decision")
    )
    content_hash: Mapped[str] = mapped_column(String(64))
    approver_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    approver_role: Mapped[str] = mapped_column(String(40))
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    note: Mapped[str | None] = mapped_column(String(500))
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
