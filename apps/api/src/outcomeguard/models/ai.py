"""AI call log, agreement drafts and evidence analyses.

AI output is stored separately from the facts it talks about: verification_results are
never modified by AI; analyses reference them. All three tables are append-only, except
the draft's review status (proposed → accepted/discarded), enforced by triggers.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from outcomeguard.models.base import Base, IdMixin, enum_col


class AICall(IdMixin, Base):
    """One model call. Stores a hash of the prompt, never the prompt or the API key."""

    __tablename__ = "ai_calls"

    task: Mapped[str] = mapped_column(String(40))  # agreement_draft | evidence_analysis
    prompt_version: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(40))
    model_requested: Mapped[str] = mapped_column(String(80))
    model_used: Mapped[str | None] = mapped_column(String(80))
    outcome: Mapped[str] = mapped_column(String(30))  # ok | invalid_output | timeout | ...
    stop_reason: Mapped[str | None] = mapped_column(String(30))
    latency_ms: Mapped[int] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer)
    request_id: Mapped[str | None] = mapped_column(String(100))
    prompt_sha256: Mapped[str] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(String(500))
    actor: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), index=True
    )


class DraftStatus(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"  # buyer turned it into an agreement DRAFT (still not locked)
    DISCARDED = "discarded"
    FAILED = "failed"  # AI unavailable / invalid output — nothing usable


class AgreementDraft(IdMixin, Base):
    __tablename__ = "agreement_drafts"

    buyer_organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    requirements_text: Mapped[str] = mapped_column(Text)
    ai_call_id: Mapped[str | None] = mapped_column(ForeignKey("ai_calls.id"))
    status: Mapped[DraftStatus] = mapped_column(enum_col(DraftStatus, "agreement_draft_status"))
    # The model's validated output, untouched.
    ai_output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Deterministic post-validation: kept / rejected criteria and why.
    review: Mapped[dict[str, Any]] = mapped_column(JSONB)
    accepted_agreement_id: Mapped[str | None] = mapped_column(ForeignKey("agreements.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class AnalysisStatus(StrEnum):
    GROUNDED = "grounded"  # every claim validated against real results
    PARTIALLY_REJECTED = "partially_rejected"  # some claims failed grounding and were removed
    REJECTED = "rejected"  # nothing usable survived grounding
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    AI_UNAVAILABLE = "ai_unavailable"


class EvidenceAnalysis(IdMixin, Base):
    __tablename__ = "evidence_analyses"

    run_id: Mapped[str] = mapped_column(ForeignKey("verification_runs.id"), index=True)
    ai_call_id: Mapped[str | None] = mapped_column(ForeignKey("ai_calls.id"))
    status: Mapped[AnalysisStatus] = mapped_column(enum_col(AnalysisStatus, "analysis_status"))
    # The deterministic verdict at analysis time, copied for display; AI cannot alter it.
    run_verdict: Mapped[str | None] = mapped_column(String(30))
    ai_output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # raw validated output
    grounded: Mapped[dict[str, Any]] = mapped_column(JSONB)  # what survived + rejections
    created_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
