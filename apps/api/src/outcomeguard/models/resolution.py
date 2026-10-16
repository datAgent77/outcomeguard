"""P08 resolution records: cases, their append-only timeline, proposals and per-side
approvals, financial policy decisions (refund / void) and governed authorization voids.

Money never moves because a case exists. A refund needs an accepted proposal approved by
both sides, a fresh `refund` policy decision and an existing PayPal capture — enforced in
the service and by database triggers (see the P08 migration)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
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
from sqlalchemy.orm import Mapped, mapped_column

from outcomeguard.models.base import Base, IdMixin, TimestampMixin, enum_col


class CaseStatus(StrEnum):
    OPEN = "open"
    UNDER_REVIEW = "under_review"
    PROPOSED = "proposed"
    AGREED = "agreed"
    EXECUTING = "executing"
    ACTION_REQUIRED = "action_required"
    RESOLVED = "resolved"
    CLOSED_NO_ACTION = "closed_no_action"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


TERMINAL_CASE_STATUSES = (
    CaseStatus.RESOLVED,
    CaseStatus.CLOSED_NO_ACTION,
    CaseStatus.REJECTED,
    CaseStatus.CANCELLED,
)


class ReasonCategory(StrEnum):
    CRITERION_NOT_MET = "criterion_not_met"
    DEFECT_AFTER_ACCEPTANCE = "defect_after_acceptance"
    INCOMPLETE_DELIVERY = "incomplete_delivery"
    LATE_DELIVERY = "late_delivery"
    CANCEL_BEFORE_CAPTURE = "cancel_before_capture"
    OTHER = "other"


class Remedy(StrEnum):
    REWORK = "rework"
    NEW_VERIFICATION = "new_verification"
    PARTIAL_REFUND = "partial_refund"
    FULL_REFUND = "full_refund"
    CANCEL_AUTHORIZATION = "cancel_authorization"
    OTHER = "other"  # negotiated, no money movement through OutcomeGuard
    NO_REMEDY = "no_remedy"


MONETARY_REMEDIES = (Remedy.PARTIAL_REFUND, Remedy.FULL_REFUND, Remedy.CANCEL_AUTHORIZATION)
REFUND_REMEDIES = (Remedy.PARTIAL_REFUND, Remedy.FULL_REFUND)


class Side(StrEnum):
    BUYER = "buyer"
    PROVIDER = "provider"


class ProposalStatus(StrEnum):
    OPEN = "open"
    ACCEPTED = "accepted"  # approved by both sides
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    WITHDRAWN = "withdrawn"
    EXECUTED = "executed"  # the money operation it authorized completed


class ApprovalChoice(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class FinancialPurpose(StrEnum):
    REFUND = "refund"
    VOID = "void"


class FinancialDecision(StrEnum):
    ALLOWED = "allowed"
    BLOCKED = "blocked"


class VoidStatus(StrEnum):
    REQUESTED = "requested"  # reserved and committed; PayPal call follows
    UNKNOWN = "unknown"  # sent, outcome uncertain — reconcile before anything else
    COMPLETED = "completed"
    FAILED = "failed"  # PayPal refused; nothing changed


class ResolutionCase(IdMixin, TimestampMixin, Base):
    __tablename__ = "resolution_cases"
    __table_args__ = (
        # At most one live case per capture (sequential cases are fine).
        Index(
            "uq_resolution_cases_live_capture",
            "payment_capture_id",
            unique=True,
            postgresql_where=text(
                "payment_capture_id IS NOT NULL AND status NOT IN "
                "('resolved','closed_no_action','rejected','cancelled')"
            ),
        ),
    )

    agreement_id: Mapped[str] = mapped_column(ForeignKey("agreements.id"), index=True)
    agreement_version_id: Mapped[str] = mapped_column(ForeignKey("agreement_versions.id"))
    agreement_content_hash: Mapped[str] = mapped_column(String(64))
    payment_authorization_id: Mapped[str | None] = mapped_column(
        ForeignKey("payment_authorizations.id")
    )
    payment_capture_id: Mapped[str | None] = mapped_column(ForeignKey("payment_captures.id"))
    buyer_organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    merchant_organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    opened_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    reason_category: Mapped[ReasonCategory] = mapped_column(
        enum_col(ReasonCategory, "resolution_reason")
    )
    description: Mapped[str] = mapped_column(Text)
    requested_remedy: Mapped[Remedy] = mapped_column(enum_col(Remedy, "resolution_remedy"))
    requested_amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    # Immutable once written (trigger): what existed when the case was opened.
    evidence_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    evidence_snapshot_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[CaseStatus] = mapped_column(enum_col(CaseStatus, "resolution_status"))
    outcome: Mapped[str | None] = mapped_column(Text)
    closure_reason: Mapped[str | None] = mapped_column(Text)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(Integer, default=1)


class ResolutionEvent(IdMixin, Base):
    """Append-only case timeline (messages, evidence links, status changes, decisions)."""

    __tablename__ = "resolution_events"

    case_id: Mapped[str] = mapped_column(ForeignKey("resolution_cases.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    author_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    side: Mapped[str | None] = mapped_column(String(20))  # buyer | provider | system
    body: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )


class ResolutionProposal(IdMixin, Base):
    __tablename__ = "resolution_proposals"
    __table_args__ = (UniqueConstraint("case_id", "number"),)

    case_id: Mapped[str] = mapped_column(ForeignKey("resolution_cases.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    author_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    side: Mapped[Side] = mapped_column(enum_col(Side, "resolution_side"))
    remedy: Mapped[Remedy] = mapped_column(enum_col(Remedy, "resolution_remedy"))
    amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    rationale: Mapped[str] = mapped_column(Text)
    evidence_refs: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    content_sha256: Mapped[str] = mapped_column(String(64))
    case_revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[ProposalStatus] = mapped_column(
        enum_col(ProposalStatus, "resolution_proposal_status")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResolutionApproval(IdMixin, Base):
    """One side's decision on one proposal, bound to the content hash it saw."""

    __tablename__ = "resolution_approvals"
    __table_args__ = (UniqueConstraint("proposal_id", "side"),)

    proposal_id: Mapped[str] = mapped_column(ForeignKey("resolution_proposals.id"), index=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("resolution_cases.id"))
    side: Mapped[Side] = mapped_column(enum_col(Side, "resolution_side"))
    choice: Mapped[ApprovalChoice] = mapped_column(enum_col(ApprovalChoice, "approval_choice"))
    approver_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    approver_role: Mapped[str] = mapped_column(String(40))
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    proposal_content_sha256: Mapped[str] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )


class FinancialPolicyDecision(IdMixin, Base):
    """Append-only evaluation of the refund / void policy."""

    __tablename__ = "financial_policy_decisions"

    purpose: Mapped[FinancialPurpose] = mapped_column(
        enum_col(FinancialPurpose, "financial_purpose")
    )
    decision: Mapped[FinancialDecision] = mapped_column(
        enum_col(FinancialDecision, "financial_decision")
    )
    reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB)
    inputs_sha256: Mapped[str] = mapped_column(String(64), index=True)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("resolution_cases.id"))
    proposal_id: Mapped[str | None] = mapped_column(ForeignKey("resolution_proposals.id"))
    payment_authorization_id: Mapped[str | None] = mapped_column(
        ForeignKey("payment_authorizations.id")
    )
    payment_capture_id: Mapped[str | None] = mapped_column(ForeignKey("payment_captures.id"))
    evaluated_by: Mapped[str] = mapped_column(String(200))
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )


class AuthorizationVoid(IdMixin, TimestampMixin, Base):
    __tablename__ = "authorization_voids"

    payment_authorization_id: Mapped[str] = mapped_column(
        ForeignKey("payment_authorizations.id"), unique=True
    )
    request_id: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[VoidStatus] = mapped_column(enum_col(VoidStatus, "void_status"))
    requested_by: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str] = mapped_column(Text)
    policy_decision_id: Mapped[str] = mapped_column(ForeignKey("financial_policy_decisions.id"))
    case_id: Mapped[str | None] = mapped_column(ForeignKey("resolution_cases.id"))
    proposal_id: Mapped[str | None] = mapped_column(ForeignKey("resolution_proposals.id"))
    paypal_status: Mapped[str | None] = mapped_column(String(40))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
