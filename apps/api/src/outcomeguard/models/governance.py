"""P06 governance records: manual-review decisions, policy decisions, capture approvals.

All three are append-only (Postgres triggers), except a capture approval's one-time
`consumed_by_capture_id` stamp. A decision binds to exact ids AND hashes, so any change to
what was reviewed or approved makes it inapplicable instead of silently reused.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from outcomeguard.models.base import Base, IdMixin, enum_col


class ReviewDecision(StrEnum):
    SATISFIED = "satisfied"
    NOT_SATISFIED = "not_satisfied"


class ManualReviewDecision(IdMixin, Base):
    """An authorized human's decision on one manual-review criterion of one run."""

    __tablename__ = "manual_review_decisions"
    __table_args__ = (
        UniqueConstraint(
            "verification_run_id", "criterion_id", name="uq_manual_review_run_criterion"
        ),
    )

    agreement_version_id: Mapped[str] = mapped_column(ForeignKey("agreement_versions.id"))
    agreement_content_hash: Mapped[str] = mapped_column(String(64))
    criterion_id: Mapped[str] = mapped_column(ForeignKey("acceptance_criteria.id"))
    verification_run_id: Mapped[str] = mapped_column(ForeignKey("verification_runs.id"), index=True)
    # Hash of the run's results + artifact hashes the reviewer was shown.
    evidence_snapshot_sha256: Mapped[str] = mapped_column(String(64))
    decision: Mapped[ReviewDecision] = mapped_column(enum_col(ReviewDecision, "review_decision"))
    rationale: Mapped[str] = mapped_column(Text)
    reviewer_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    reviewer_role: Mapped[str] = mapped_column(String(40))
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class PolicyOutcome(StrEnum):
    BLOCKED = "blocked"
    NEEDS_REVIEW = "needs_review"
    ELIGIBLE_FOR_APPROVAL = "eligible_for_approval"
    CAPTURE_APPROVED = "capture_approved"  # re-evaluated at capture time, capture reserved
    CAPTURED = "captured"  # PayPal reported the capture completed


class PolicyDecisionRecord(IdMixin, Base):
    """One evaluation of the deterministic policy. Evidence of what was checked, never a
    standing permission: capture always re-evaluates."""

    __tablename__ = "policy_decisions"

    payment_authorization_id: Mapped[str] = mapped_column(
        ForeignKey("payment_authorizations.id"), index=True
    )
    agreement_version_id: Mapped[str | None] = mapped_column(ForeignKey("agreement_versions.id"))
    verification_run_id: Mapped[str | None] = mapped_column(ForeignKey("verification_runs.id"))
    purpose: Mapped[str] = mapped_column(String(20))  # preview | approval | capture | settlement
    decision: Mapped[PolicyOutcome] = mapped_column(enum_col(PolicyOutcome, "policy_outcome"))
    reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB)
    inputs_sha256: Mapped[str] = mapped_column(String(64), index=True)
    evaluated_by: Mapped[str] = mapped_column(String(200))
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class CaptureApproval(IdMixin, Base):
    """The buyer's explicit approval to capture, bound to one authorization and the exact
    policy inputs hash the approver saw. Consumed once by a capture."""

    __tablename__ = "capture_approvals"
    __table_args__ = (
        UniqueConstraint(
            "payment_authorization_id", "inputs_sha256", name="uq_capture_approval_auth_inputs"
        ),
    )

    payment_authorization_id: Mapped[str] = mapped_column(
        ForeignKey("payment_authorizations.id"), index=True
    )
    policy_decision_id: Mapped[str] = mapped_column(ForeignKey("policy_decisions.id"))
    inputs_sha256: Mapped[str] = mapped_column(String(64))
    approver_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    approver_role: Mapped[str] = mapped_column(String(40))
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    consumed_by_capture_id: Mapped[str | None] = mapped_column(String(36))
