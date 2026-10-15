"""Structured outputs the model must return. Validated with Pydantic after every call.

Design rule: the schemas contain NO field that can express a verdict, a payment action,
a selector, a URL or code. The model can only *describe*; deterministic code decides.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SupportedKind = Literal[
    "http_availability",
    "mobile_viewport",
    "contact_form",
    "lighthouse_performance",
    "manual_review",
]
Outcome = Literal["pass", "fail", "error", "not_run", "manual_review_required"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------- agreement drafting


class ExpectedValues(_Strict):
    """Only values the buyer actually stated. Absent → null, never guessed."""

    min_performance_score: int | None = Field(
        default=None, description="Lighthouse threshold only if the request states a number"
    )
    form_factor: Literal["mobile", "desktop"] | None = None


class DraftCriterion(_Strict):
    key: str = Field(description="snake_case identifier, e.g. contact_form_works")
    title: str = Field(description="One-line human-readable criterion")
    kind: SupportedKind
    origin: Literal["stated", "suggested"] = Field(
        description="stated: the buyer explicitly asked for this. suggested: your own "
        "recommendation that the buyer did not ask for"
    )
    blocking: bool = Field(description="True if the promise depends on it")
    source_quote: str = Field(
        description="Exact, verbatim words from the buyer's request that this criterion is "
        "based on (for suggestions: the words that motivated the suggestion)"
    )
    rationale: str | None = Field(
        default=None, description="For suggestions: why it would help the buyer"
    )
    expected: ExpectedValues
    needs_configuration: list[str] = Field(
        description="Technical details the buyer must supply before this can be verified "
        "(e.g. 'contact form field selectors', 'success message text'). Empty if none."
    )
    manual_review_instructions: str | None = Field(
        default=None, description="Only for kind=manual_review: what a human should check"
    )


class Ambiguity(_Strict):
    topic: str
    question: str = Field(description="A question to ask the buyer")


class ManualItem(_Strict):
    requirement: str = Field(description="Verbatim requirement from the request")
    reason: str


class AgreementDraftOut(_Strict):
    service_summary: str
    deliverables: list[str]
    criteria: list[DraftCriterion]
    ambiguities: list[Ambiguity]
    manual_review_items: list[ManualItem]
    unsupported_requirements: list[ManualItem]


# ------------------------------------------------------------- evidence analysis


class EvidenceRef(_Strict):
    result_id: str = Field(description="id of a verification result from the input")
    measurement: str | None = Field(
        default=None,
        description="Name of a measured field of that result, e.g. sink_requests",
    )
    artifact_id: str | None = Field(default=None, description="id of an artifact from the input")


class CriterionExplanation(_Strict):
    criterion_key: str
    result_id: str
    outcome_reported: Outcome = Field(
        description="Copy the deterministic outcome exactly as given in the input"
    )
    explanation: str = Field(description="What the measurements show, citing evidence_refs")
    evidence_refs: list[EvidenceRef]
    remediation: str | None = Field(
        default=None, description="Concrete fix for the provider, only for non-passing criteria"
    )
    confidence: Literal["low", "medium", "high"]
    insufficient_evidence: bool = Field(
        description="True when the measurements do not support a conclusion"
    )


class EvidenceAnalysisOut(_Strict):
    summary: str = Field(description="Plain-language summary for the buyer and provider")
    criteria: list[CriterionExplanation]
    missing_evidence: list[str]
    limitations: list[str]
