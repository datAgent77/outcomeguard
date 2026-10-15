"""Deterministic verification specifications for acceptance criteria.

Each criterion has a `kind` and a typed `spec`. Specs are *data* for the verification
worker (P04): selectors, thresholds, viewport sizes. They never contain code, shell
commands, or arbitrary URLs — targets are paths relative to the evidence URL the provider
submits, and the contact-form check may only submit to an operator-configured test
endpoint referenced by name.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator


class CriterionKind(StrEnum):
    HTTP_AVAILABILITY = "http_availability"
    MOBILE_VIEWPORT = "mobile_viewport"
    CONTACT_FORM = "contact_form"
    LIGHTHOUSE_PERFORMANCE = "lighthouse_performance"
    MANUAL_REVIEW = "manual_review"


class VerificationMethod(StrEnum):
    AUTOMATED = "automated"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"


Path_ = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^/[A-Za-z0-9._~/%-]*$")]
Selector = Annotated[str, Field(min_length=1, max_length=200)]


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HttpAvailabilitySpec(_Spec):
    kind: Literal[CriterionKind.HTTP_AVAILABILITY] = CriterionKind.HTTP_AVAILABILITY
    path: Path_ = "/"
    expected_status: list[Annotated[int, Field(ge=200, le=399)]] = Field(
        default_factory=lambda: [200], min_length=1, max_length=5
    )
    max_redirects: int = Field(default=5, ge=0, le=10)
    require_https: bool = True
    timeout_ms: int = Field(default=10_000, ge=1_000, le=30_000)


class Viewport(_Spec):
    width: int = Field(ge=280, le=1024)
    height: int = Field(ge=480, le=1400)


class MobileViewportSpec(_Spec):
    kind: Literal[CriterionKind.MOBILE_VIEWPORT] = CriterionKind.MOBILE_VIEWPORT
    path: Path_ = "/"
    viewport: Viewport = Viewport(width=390, height=844)
    no_horizontal_overflow: bool = True
    required_visible_selectors: list[Selector] = Field(default_factory=list, max_length=10)
    timeout_ms: int = Field(default=20_000, ge=1_000, le=60_000)


class FormField(_Spec):
    selector: Selector
    value: Annotated[str, Field(max_length=500)]


class ContactFormSpec(_Spec):
    kind: Literal[CriterionKind.CONTACT_FORM] = CriterionKind.CONTACT_FORM
    path: Path_ = "/"
    form_selector: Selector = "form"
    fields: list[FormField] = Field(min_length=1, max_length=10)
    submit_selector: Selector = "button[type=submit]"
    success_text: Annotated[str, Field(min_length=1, max_length=200)]
    # Name of an operator-configured sink the form must post to (never a free URL).
    test_endpoint_ref: Annotated[str, Field(pattern=r"^[a-z0-9-]{1,40}$")] = "default"
    timeout_ms: int = Field(default=20_000, ge=1_000, le=60_000)


class LighthousePerformanceSpec(_Spec):
    kind: Literal[CriterionKind.LIGHTHOUSE_PERFORMANCE] = CriterionKind.LIGHTHOUSE_PERFORMANCE
    path: Path_ = "/"
    min_score: int = Field(ge=0, le=100)
    form_factor: Literal["mobile", "desktop"] = "mobile"
    runs: int = Field(default=3, ge=1, le=5)  # median of N runs
    throttling: Literal["simulate"] = "simulate"


class ManualReviewSpec(_Spec):
    kind: Literal[CriterionKind.MANUAL_REVIEW] = CriterionKind.MANUAL_REVIEW
    instructions: Annotated[str, Field(min_length=1, max_length=2000)]


CriterionSpec = Annotated[
    HttpAvailabilitySpec
    | MobileViewportSpec
    | ContactFormSpec
    | LighthousePerformanceSpec
    | ManualReviewSpec,
    Field(discriminator="kind"),
]
_spec_adapter: TypeAdapter[CriterionSpec] = TypeAdapter(CriterionSpec)


def parse_spec(data: object) -> CriterionSpec:
    return _spec_adapter.validate_python(data)


def method_for(kind: CriterionKind) -> VerificationMethod:
    return (
        VerificationMethod.MANUAL_REVIEW_REQUIRED
        if kind is CriterionKind.MANUAL_REVIEW
        else VerificationMethod.AUTOMATED
    )


class CriterionInput(BaseModel):
    """A criterion as submitted by a client (buyer) or, later, proposed by AI."""

    model_config = ConfigDict(extra="forbid")

    key: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,49}$")]
    title: Annotated[str, Field(min_length=1, max_length=200)]
    blocking: bool = True
    spec: CriterionSpec

    @field_validator("title")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


def landing_page_template(
    *, min_performance_score: int = 90, success_text: str = "Thank you"
) -> list[CriterionInput]:
    """Deterministic default criteria for the landing-page service category.

    This is a fixed template, not AI output; P05 adds AI-proposed criteria that the buyer
    reviews the same way.
    """
    return [
        CriterionInput(
            key="site_available",
            title="Landing page is reachable over HTTPS",
            spec=HttpAvailabilitySpec(),
        ),
        CriterionInput(
            key="mobile_layout",
            title="Layout fits a 390×844 mobile viewport without horizontal scrolling",
            spec=MobileViewportSpec(),
        ),
        CriterionInput(
            key="contact_form_works",
            title="Contact form submits successfully",
            spec=ContactFormSpec(
                fields=[
                    FormField(selector="input[name=name]", value="OutcomeGuard Verifier"),
                    FormField(selector="input[name=email]", value="verifier@example.com"),
                    FormField(selector="textarea[name=message]", value="Automated check"),
                ],
                success_text=success_text,
            ),
        ),
        CriterionInput(
            key="lighthouse_performance",
            title=f"Lighthouse mobile performance score ≥ {min_performance_score}",
            spec=LighthousePerformanceSpec(min_score=min_performance_score),
        ),
    ]
