"""Evidence submissions, verification runs, results, artifacts, jobs and form-sink receipts.

Append-only / immutable rules are enforced by Postgres triggers (P04 migration):
* evidence_submissions, evidence_artifacts, verification_results, form_sink_receipts:
  no UPDATE or DELETE;
* verification_results can only be inserted while their run is `running`;
* a run in a terminal status (completed / errored) can no longer change.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from outcomeguard.domain.verdict import CriterionOutcome, RunVerdict
from outcomeguard.models.base import Base, IdMixin, TimestampMixin, enum_col


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"  # every criterion evaluated; verdict set
    ERRORED = "errored"  # gave up (crash / timeouts / max attempts); verdict INCONCLUSIVE


TERMINAL_RUN_STATUSES = (RunStatus.COMPLETED, RunStatus.ERRORED)


class JobState(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    DONE = "done"
    FAILED = "failed"


class ArtifactKind(StrEnum):
    SCREENSHOT = "screenshot"
    LIGHTHOUSE_JSON = "lighthouse_json"
    HTTP_TRACE = "http_trace"
    NETWORK_LOG = "network_log"


class EvidenceSubmission(IdMixin, Base):
    __tablename__ = "evidence_submissions"

    agreement_version_id: Mapped[str] = mapped_column(
        ForeignKey("agreement_versions.id"), index=True
    )
    submitted_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    delivery_url: Mapped[str] = mapped_column(String(2048))
    repo_url: Mapped[str | None] = mapped_column(String(2048))
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    notes: Mapped[str | None] = mapped_column(Text)
    submission_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class VerificationRun(IdMixin, TimestampMixin, Base):
    __tablename__ = "verification_runs"

    submission_id: Mapped[str] = mapped_column(ForeignKey("evidence_submissions.id"), index=True)
    agreement_version_id: Mapped[str] = mapped_column(
        ForeignKey("agreement_versions.id"), index=True
    )
    # Copied from the locked version: the run is bound to exactly this content.
    agreement_content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[RunStatus] = mapped_column(enum_col(RunStatus, "verification_run_status"))
    verdict: Mapped[RunVerdict | None] = mapped_column(enum_col(RunVerdict, "run_verdict"))
    worker_id: Mapped[str | None] = mapped_column(String(100))
    tool_versions: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    config: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)

    results: Mapped[list[VerificationResult]] = relationship(
        back_populates="run", order_by="VerificationResult.position"
    )


class VerificationResult(IdMixin, Base):
    __tablename__ = "verification_results"

    run_id: Mapped[str] = mapped_column(ForeignKey("verification_runs.id"), index=True)
    criterion_id: Mapped[str] = mapped_column(ForeignKey("acceptance_criteria.id"))
    position: Mapped[int] = mapped_column(Integer)
    criterion_key: Mapped[str] = mapped_column(String(50))
    kind: Mapped[str] = mapped_column(String(40))
    blocking: Mapped[bool] = mapped_column(Boolean)
    outcome: Mapped[CriterionOutcome] = mapped_column(
        enum_col(CriterionOutcome, "criterion_outcome")
    )
    reason_code: Mapped[str] = mapped_column(String(60))
    measured: Mapped[dict[str, Any]] = mapped_column(JSONB)
    expected: Mapped[dict[str, Any]] = mapped_column(JSONB)
    artifact_ids: Mapped[list[str]] = mapped_column(JSONB)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    run: Mapped[VerificationRun] = relationship(back_populates="results")


class EvidenceArtifact(IdMixin, Base):
    __tablename__ = "evidence_artifacts"

    run_id: Mapped[str] = mapped_column(ForeignKey("verification_runs.id"), index=True)
    kind: Mapped[ArtifactKind] = mapped_column(enum_col(ArtifactKind, "artifact_kind"))
    label: Mapped[str] = mapped_column(String(120))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    content_type: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class VerificationJob(IdMixin, TimestampMixin, Base):
    __tablename__ = "verification_jobs"

    run_id: Mapped[str] = mapped_column(ForeignKey("verification_runs.id"), unique=True)
    state: Mapped[JobState] = mapped_column(enum_col(JobState, "verification_job_state"))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2)
    lease_owner: Mapped[str | None] = mapped_column(String(100))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    last_error: Mapped[str | None] = mapped_column(Text)


class FormSinkReceipt(IdMixin, Base):
    """What the controlled form sink received. Field *names* and a hash of the values —
    never the free-form values themselves."""

    __tablename__ = "form_sink_receipts"

    run_id: Mapped[str] = mapped_column(ForeignKey("verification_runs.id"), index=True)
    sink_ref: Mapped[str] = mapped_column(String(40))
    field_names: Mapped[list[str]] = mapped_column(JSONB)
    values_sha256: Mapped[str] = mapped_column(String(64))
    content_length: Mapped[int] = mapped_column(Integer)
    origin: Mapped[str | None] = mapped_column(String(300))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
