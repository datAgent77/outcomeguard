"""Deterministic verdicts. Pure functions — no I/O, no AI, no clock.

A criterion outcome is computed from *measurements* (what the runner observed) and the
criterion's spec. The run verdict is computed from the outcomes. PASS is only ever the
result of every blocking criterion having positively passed; anything missing, errored or
uncertain can never become PASS.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class CriterionOutcome(StrEnum):
    PASS = "pass"  # noqa: S105 — enum value, not a secret
    FAIL = "fail"
    ERROR = "error"  # the check could not be performed (tool/network failure)
    NOT_RUN = "not_run"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"


class RunVerdict(StrEnum):
    PASS = "pass"  # noqa: S105 — enum value, not a secret
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"
    NEEDS_MANUAL_REVIEW = "needs_manual_review"


@dataclass(frozen=True)
class Evaluation:
    outcome: CriterionOutcome
    reason_code: str
    detail: dict[str, Any]


def _err(m: dict[str, Any]) -> Evaluation | None:
    if m.get("error"):
        return Evaluation(
            CriterionOutcome.ERROR, str(m.get("error_code") or "CHECK_ERROR"), {"error": m["error"]}
        )
    return None


def evaluate_http(spec: dict[str, Any], m: dict[str, Any]) -> Evaluation:
    if e := _err(m):
        return e
    chain = m.get("chain") or []
    if not chain:
        return Evaluation(CriterionOutcome.ERROR, "NO_RESPONSE", {})
    final = chain[-1]
    redirects = len(chain) - 1
    if redirects > spec["max_redirects"]:
        return Evaluation(CriterionOutcome.FAIL, "TOO_MANY_REDIRECTS", {"redirects": redirects})
    if spec.get("require_https", True) and not str(final.get("url", "")).startswith("https://"):
        return Evaluation(CriterionOutcome.FAIL, "NOT_HTTPS", {"final_url": final.get("url")})
    status = final.get("status")
    if status not in spec["expected_status"]:
        return Evaluation(CriterionOutcome.FAIL, "UNEXPECTED_STATUS", {"status": status})
    return Evaluation(CriterionOutcome.PASS, "OK", {"status": status, "redirects": redirects})


def evaluate_mobile(spec: dict[str, Any], m: dict[str, Any]) -> Evaluation:
    if e := _err(m):
        return e
    if "scroll_width" not in m or "viewport_width" not in m:
        return Evaluation(CriterionOutcome.ERROR, "MISSING_MEASUREMENT", {})
    if spec.get("no_horizontal_overflow", True) and m["scroll_width"] > m["viewport_width"] + 1:
        return Evaluation(
            CriterionOutcome.FAIL,
            "HORIZONTAL_OVERFLOW",
            {"scroll_width": m["scroll_width"], "viewport_width": m["viewport_width"]},
        )
    visible = m.get("selectors_visible") or {}
    missing = [s for s in spec.get("required_visible_selectors", []) if not visible.get(s)]
    if missing:
        return Evaluation(
            CriterionOutcome.FAIL, "REQUIRED_ELEMENT_NOT_VISIBLE", {"missing": missing}
        )
    return Evaluation(CriterionOutcome.PASS, "OK", {})


def evaluate_contact_form(
    spec: dict[str, Any], m: dict[str, Any], sink_receipts: int
) -> Evaluation:
    """`sink_receipts` is read from the database by the orchestrator, not reported by the
    runner — the browser cannot fake a receipt."""
    # A prohibited destination is a definite failure even if other things errored.
    if m.get("prohibited_requests"):
        return Evaluation(
            CriterionOutcome.FAIL,
            "FORM_POSTS_TO_UNAPPROVED_DESTINATION",
            {"blocked": m["prohibited_requests"][:5]},
        )
    if m.get("unverifiable"):
        return Evaluation(CriterionOutcome.MANUAL_REVIEW_REQUIRED, str(m["unverifiable"]), {})
    if e := _err(m):
        return e
    if not m.get("form_found"):
        return Evaluation(CriterionOutcome.FAIL, "FORM_NOT_FOUND", {})
    missing = m.get("fields_missing") or []
    if missing:
        return Evaluation(CriterionOutcome.FAIL, "FORM_FIELD_NOT_FOUND", {"missing": missing})
    sink_statuses = [r.get("status") for r in m.get("sink_requests") or []]
    if not sink_statuses:
        return Evaluation(
            CriterionOutcome.FAIL,
            "NO_SUBMISSION_TO_SINK",
            {"success_text_seen": bool(m.get("success_text_seen"))},
        )
    if not any(isinstance(s, int) and 200 <= s < 300 for s in sink_statuses):
        return Evaluation(
            CriterionOutcome.FAIL, "SINK_REJECTED_SUBMISSION", {"statuses": sink_statuses}
        )
    if sink_receipts < 1:
        return Evaluation(CriterionOutcome.FAIL, "NO_SINK_RECEIPT", {})
    if not m.get("success_text_seen"):
        return Evaluation(CriterionOutcome.FAIL, "SUCCESS_TEXT_NOT_SHOWN", {})
    return Evaluation(CriterionOutcome.PASS, "OK", {"sink_receipts": sink_receipts})


def lighthouse_needs_more_runs(score: int, min_score: int, band: int) -> bool:
    return abs(score - min_score) <= band


def evaluate_lighthouse(spec: dict[str, Any], m: dict[str, Any]) -> Evaluation:
    if e := _err(m):
        return e
    scores = [s for s in m.get("scores") or [] if isinstance(s, int)]
    if not scores:
        return Evaluation(CriterionOutcome.ERROR, "NO_SCORE", {})
    if (
        m.get("band") is not None
        and len(scores) == 1
        and lighthouse_needs_more_runs(scores[0], spec["min_score"], int(m["band"]))
    ):
        # Near the threshold the protocol requires 3 measurements; one is not enough.
        return Evaluation(
            CriterionOutcome.ERROR, "INSUFFICIENT_RUNS_NEAR_THRESHOLD", {"scores": scores}
        )
    median = int(statistics.median(scores))
    detail = {"scores": scores, "median": median, "min_score": spec["min_score"]}
    if median >= spec["min_score"]:
        return Evaluation(CriterionOutcome.PASS, "OK", detail)
    return Evaluation(CriterionOutcome.FAIL, "SCORE_BELOW_THRESHOLD", detail)


def evaluate(
    kind: str, spec: dict[str, Any], measurement: dict[str, Any] | None, *, sink_receipts: int = 0
) -> Evaluation:
    if kind == "manual_review":
        return Evaluation(CriterionOutcome.MANUAL_REVIEW_REQUIRED, "MANUAL_CRITERION", {})
    if measurement is None:
        return Evaluation(CriterionOutcome.NOT_RUN, "NOT_MEASURED", {})
    if kind == "http_availability":
        return evaluate_http(spec, measurement)
    if kind == "mobile_viewport":
        return evaluate_mobile(spec, measurement)
    if kind == "contact_form":
        return evaluate_contact_form(spec, measurement, sink_receipts)
    if kind == "lighthouse_performance":
        return evaluate_lighthouse(spec, measurement)
    return Evaluation(CriterionOutcome.MANUAL_REVIEW_REQUIRED, "UNKNOWN_CRITERION_KIND", {})


def run_verdict(outcomes: list[tuple[bool, CriterionOutcome]]) -> RunVerdict:
    """outcomes: (blocking, outcome) per criterion. Empty → INCONCLUSIVE."""
    blocking = [o for b, o in outcomes if b]
    if not blocking:
        return RunVerdict.INCONCLUSIVE
    if CriterionOutcome.FAIL in blocking:
        return RunVerdict.FAIL
    if any(o in (CriterionOutcome.ERROR, CriterionOutcome.NOT_RUN) for o in blocking):
        return RunVerdict.INCONCLUSIVE
    if CriterionOutcome.MANUAL_REVIEW_REQUIRED in blocking:
        return RunVerdict.NEEDS_MANUAL_REVIEW
    if all(o is CriterionOutcome.PASS for o in blocking):
        return RunVerdict.PASS
    return RunVerdict.INCONCLUSIVE  # unreachable by construction; never default to PASS
