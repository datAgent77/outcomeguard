"""Deterministic payment eligibility policy. Pure: no I/O, no clock, no AI.

`evaluate(inputs)` returns a decision and machine-readable reasons. `inputs_sha256` is the
canonical hash of everything the decision depends on EXCEPT the clock; a buyer's capture
approval binds to it, so any change in agreement, payment, evidence, reviews or capture
state yields a different hash and the old approval no longer applies. Time-dependent checks
(authorization expiry) are evaluated against `now` separately on every evaluation.

Invariant: no capture unless every applicable condition holds; PASS alone is never enough
(a separate human capture approval is always required); AI output is not an input.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

AUTH_EXPIRY_MARGIN = timedelta(hours=1)


class Decision(StrEnum):
    BLOCKED = "blocked"
    NEEDS_REVIEW = "needs_review"
    ELIGIBLE_FOR_APPROVAL = "eligible_for_approval"


@dataclass(frozen=True)
class CriterionIn:
    criterion_id: str
    key: str
    kind: str
    blocking: bool


@dataclass(frozen=True)
class ResultIn:
    criterion_id: str
    outcome: str  # pass | fail | error | not_run | manual_review_required


@dataclass(frozen=True)
class ReviewIn:
    criterion_id: str
    verification_run_id: str
    agreement_version_id: str
    agreement_content_hash: str
    evidence_snapshot_sha256: str
    decision: str  # satisfied | not_satisfied
    reviewer_is_human: bool
    reviewer_is_provider: bool


@dataclass(frozen=True)
class PolicyInputs:
    # Agreement
    agreement_id: str
    agreement_current_locked_version_id: str | None
    version_id: str | None
    version_status: str | None
    version_content_hash: str | None
    version_amount_minor: int | None
    version_currency: str | None
    agreement_merchant_account_id: str
    # Payment
    order_agreement_version_id: str | None
    order_amount_minor: int
    order_currency: str
    order_merchant_account_id: str
    merchant_paypal_id: str | None
    authorization_id: str
    authorization_status: str
    authorization_amount_minor: int
    authorization_currency: str
    authorization_payee: str | None
    authorization_expires_at: str | None  # ISO; compared with `now`, excluded from hash
    capture_status: str | None  # existing capture row status, if any
    # Evidence
    latest_submission_id: str | None
    run_id: str | None
    run_submission_id: str | None
    run_status: str | None
    run_verdict: str | None
    run_version_id: str | None
    run_content_hash: str | None
    evidence_snapshot_sha256: str | None
    criteria: tuple[CriterionIn, ...] = field(default_factory=tuple)
    results: tuple[ResultIn, ...] = field(default_factory=tuple)
    reviews: tuple[ReviewIn, ...] = field(default_factory=tuple)

    def canonical(self) -> dict:
        d = asdict(self)
        d.pop("authorization_expires_at")  # time-dependent; checked on every evaluation
        d["criteria"] = sorted(d["criteria"], key=lambda c: c["criterion_id"])
        d["results"] = sorted(d["results"], key=lambda r: r["criterion_id"])
        d["reviews"] = sorted(
            d["reviews"], key=lambda r: (r["criterion_id"], r["verification_run_id"])
        )
        return d

    def sha256(self) -> str:
        blob = json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()


@dataclass(frozen=True)
class Reason:
    code: str
    detail: str = ""


@dataclass(frozen=True)
class PolicyResult:
    decision: Decision
    reasons: tuple[Reason, ...]
    inputs_sha256: str

    @property
    def eligible(self) -> bool:
        return self.decision is Decision.ELIGIBLE_FOR_APPROVAL

    def reasons_json(self) -> list[dict]:
        return [asdict(r) for r in self.reasons]


def evaluate(i: PolicyInputs, now: datetime) -> PolicyResult:
    r: list[Reason] = []

    # --- agreement binding -----------------------------------------------------------
    if i.order_agreement_version_id is None:
        r.append(Reason("PAYMENT_NOT_BOUND_TO_AGREEMENT"))
    if i.version_id is None or i.version_status != "locked":
        r.append(Reason("AGREEMENT_NOT_LOCKED", f"version status {i.version_status}"))
    if i.version_id != i.agreement_current_locked_version_id:
        r.append(
            Reason(
                "AGREEMENT_SUPERSEDED",
                "payment is bound to a version that is no longer the locked one",
            )
        )
    if i.order_agreement_version_id != i.version_id:
        r.append(Reason("VERSION_MISMATCH"))
    if (i.order_amount_minor, i.order_currency) != (i.version_amount_minor, i.version_currency):
        r.append(Reason("AMOUNT_MISMATCH", "order amount differs from the agreement version"))
    if (i.authorization_amount_minor, i.authorization_currency) != (
        i.order_amount_minor,
        i.order_currency,
    ):
        r.append(Reason("AMOUNT_MISMATCH", "authorization amount differs from the order"))
    if i.order_merchant_account_id != i.agreement_merchant_account_id:
        r.append(Reason("MERCHANT_MISMATCH", "order merchant differs from the agreement"))
    if i.merchant_paypal_id and i.authorization_payee != i.merchant_paypal_id:
        r.append(Reason("MERCHANT_MISMATCH", "authorization payee is not the merchant"))

    # --- authorization -----------------------------------------------------------------
    if i.authorization_status != "authorized":
        r.append(Reason("AUTH_NOT_CAPTURABLE", f"authorization is {i.authorization_status}"))
    if i.authorization_expires_at is None:
        r.append(Reason("AUTH_EXPIRY_UNKNOWN"))
    elif datetime.fromisoformat(i.authorization_expires_at) <= now + AUTH_EXPIRY_MARGIN:
        r.append(Reason("AUTH_EXPIRED", "expired or within the safety margin"))
    if i.capture_status in ("completed", "pending"):
        r.append(Reason("ALREADY_CAPTURED"))
    elif i.capture_status in ("requested", "unknown"):
        r.append(Reason("CAPTURE_IN_PROGRESS", "reconcile the existing capture first"))

    # --- evidence ----------------------------------------------------------------------
    if i.run_id is None:
        r.append(Reason("NO_VERIFICATION_RUN"))
    else:
        if i.run_submission_id != i.latest_submission_id:
            r.append(Reason("STALE_EVIDENCE", "newer evidence was submitted after this run"))
        if i.run_status != "completed":
            r.append(Reason("NO_COMPLETED_RUN", f"latest run is {i.run_status}"))
        if i.run_version_id != i.version_id or i.run_content_hash != i.version_content_hash:
            r.append(
                Reason("RUN_VERSION_MISMATCH", "the run verified a different agreement version")
            )

    # --- criteria and human reviews ----------------------------------------------------
    pending_review = False
    results = {x.criterion_id: x for x in i.results}
    for c in i.criteria:
        if not c.blocking:
            continue
        res = results.get(c.criterion_id)
        if res is None:
            r.append(Reason("CRITERION_NOT_EVALUATED", c.key))
        elif res.outcome == "pass":
            continue
        elif res.outcome == "fail":
            r.append(Reason("BLOCKING_CRITERION_FAILED", c.key))
        elif res.outcome == "manual_review_required":
            valid = [
                v
                for v in i.reviews
                if v.criterion_id == c.criterion_id
                and v.verification_run_id == i.run_id
                and v.agreement_version_id == i.version_id
                and v.agreement_content_hash == i.version_content_hash
                and v.evidence_snapshot_sha256 == i.evidence_snapshot_sha256
                and v.reviewer_is_human
                and not v.reviewer_is_provider
            ]
            if not valid:
                pending_review = True
                r.append(Reason("MANUAL_REVIEW_PENDING", c.key))
            elif any(v.decision != "satisfied" for v in valid):
                r.append(Reason("MANUAL_REVIEW_REJECTED", c.key))
        else:
            r.append(Reason("VERIFICATION_INCONCLUSIVE", f"{c.key}: {res.outcome}"))

    if not any(c.blocking for c in i.criteria):
        r.append(Reason("NO_BLOCKING_CRITERIA", "nothing binding was verified"))

    blocking_codes = [x for x in r if x.code != "MANUAL_REVIEW_PENDING"]
    if blocking_codes:
        decision = Decision.BLOCKED
    elif pending_review:
        decision = Decision.NEEDS_REVIEW
    else:
        decision = Decision.ELIGIBLE_FOR_APPROVAL
    return PolicyResult(decision, tuple(r), i.sha256())
