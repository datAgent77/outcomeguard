"""Deterministic refund / void policy and the resolution-case state machine (P08).

Pure functions of plain inputs: no database, no PayPal, no clock. The service gathers the
inputs under the relevant row locks, evaluates, persists the decision, and only then
reserves the money operation. `inputs_sha256` identifies exactly what was evaluated.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum

# ----------------------------------------------------------------- case states

TERMINAL = frozenset({"resolved", "closed_no_action", "rejected", "cancelled"})

# event -> {from_state: to_state}
TRANSITIONS: dict[str, dict[str, str]] = {
    "counterparty_activity": {"open": "under_review"},
    "proposal_submitted": {
        "open": "proposed",
        "under_review": "proposed",
        "proposed": "proposed",  # a newer proposal supersedes the open one
        "agreed": "proposed",  # only while nothing was executed (service checks)
        "action_required": "proposed",
    },
    "proposal_rejected": {"proposed": "under_review"},
    "proposal_withdrawn": {"proposed": "under_review"},
    "proposal_accepted": {"proposed": "agreed"},
    "execution_reserved": {"agreed": "executing", "action_required": "executing"},
    "execution_completed": {"executing": "resolved", "action_required": "resolved"},
    "execution_uncertain": {"executing": "action_required"},
    "execution_failed": {"executing": "action_required"},
    "closed_resolved": {"agreed": "resolved"},  # non-monetary remedy carried out
    "withdrawn_by_buyer": {
        "open": "cancelled",
        "under_review": "cancelled",
        "proposed": "cancelled",
    },
    "closed_no_action": {
        "open": "closed_no_action",
        "under_review": "closed_no_action",
        "proposed": "closed_no_action",
        "agreed": "closed_no_action",
    },
    "claim_rejected": {"under_review": "rejected", "proposed": "rejected"},
}


class TransitionError(Exception):
    def __init__(self, event: str, state: str) -> None:
        super().__init__(f"cannot apply {event!r} to a case that is {state!r}")
        self.event, self.state = event, state


def next_state(state: str, event: str) -> str:
    """The case state after `event`, or TransitionError. Same-state moves are explicit."""
    to = TRANSITIONS.get(event, {}).get(state)
    if to is None:
        raise TransitionError(event, state)
    return to


# ---------------------------------------------------------------- decisions


class FinDecision(StrEnum):
    ALLOWED = "allowed"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class Reason:
    code: str
    detail: str = ""


@dataclass(frozen=True)
class FinResult:
    decision: FinDecision
    reasons: tuple[Reason, ...]
    inputs_sha256: str
    figures: dict[str, int] = field(default_factory=dict)


def _sha(obj: object) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def _result(reasons: list[Reason], inputs: object, figures: dict[str, int]) -> FinResult:
    return FinResult(
        FinDecision.BLOCKED if reasons else FinDecision.ALLOWED,
        tuple(reasons),
        _sha(asdict(inputs)),  # type: ignore[call-overload]
        figures,
    )


# ------------------------------------------------------------------- refund

# Refund statuses that hold part of the captured amount (mirrors REFUND_HOLDS_BALANCE).
HOLDING = frozenset({"requested", "unknown", "pending", "completed", "reconciliation_required"})
UNCERTAIN = frozenset({"unknown", "reconciliation_required"})
IN_FLIGHT = frozenset({"requested", "pending"})


@dataclass(frozen=True)
class ApprovalIn:
    side: str  # buyer | provider
    choice: str  # approve | reject
    content_sha256: str
    human: bool
    organization_matches: bool


@dataclass(frozen=True)
class RefundIn:
    status: str
    amount_minor: int
    currency: str
    proposal_id: str | None


@dataclass(frozen=True)
class RefundInputs:
    actor_authorized: bool
    case_id: str | None
    case_status: str | None
    case_agreement_id: str | None
    case_capture_id: str | None
    proposal_id: str | None
    proposal_case_id: str | None
    proposal_status: str | None
    proposal_remedy: str | None
    proposal_amount_minor: int | None
    proposal_currency: str | None
    proposal_content_sha256: str | None
    approvals: tuple[ApprovalIn, ...]
    capture_id: str | None
    capture_status: str | None
    capture_agreement_id: str | None
    capture_amount_minor: int
    capture_currency: str | None
    refunds: tuple[RefundIn, ...]

    def sha256(self) -> str:
        return _sha(asdict(self))


def evaluate_refund(i: RefundInputs) -> FinResult:
    r: list[Reason] = []
    held = sum(x.amount_minor for x in i.refunds if x.status in HOLDING)
    refunded = sum(x.amount_minor for x in i.refunds if x.status == "completed")
    remaining = i.capture_amount_minor - held
    figures = {
        "captured_minor": i.capture_amount_minor,
        "refunded_minor": refunded,
        "held_minor": held - refunded,
        "remaining_minor": remaining,
        "requested_minor": i.proposal_amount_minor or 0,
    }
    if not i.actor_authorized:
        r.append(Reason("ACTOR_NOT_AUTHORIZED"))
    if i.case_id is None:
        r.append(Reason("RESOLUTION_NOT_FOUND"))
        return _result(r, i, figures)
    if i.case_status not in ("agreed", "action_required"):
        r.append(Reason("RESOLUTION_NOT_AGREED", f"case is {i.case_status}"))
    if i.proposal_id is None or i.proposal_case_id != i.case_id:
        r.append(Reason("RESOLUTION_NOT_AGREED", "no proposal of this case"))
        return _result(r, i, figures)
    if i.proposal_remedy not in ("partial_refund", "full_refund"):
        r.append(Reason("REFUND_AMOUNT_INVALID", f"remedy is {i.proposal_remedy}"))
    if i.proposal_status == "executed":
        r.append(Reason("REFUND_ALREADY_COMPLETED"))
    elif i.proposal_status != "accepted":
        r.append(Reason("RESOLUTION_NOT_AGREED", f"proposal is {i.proposal_status}"))
    for side in ("buyer", "provider"):
        valid = [
            a
            for a in i.approvals
            if a.side == side
            and a.choice == "approve"
            and a.human
            and a.organization_matches
            and a.content_sha256 == i.proposal_content_sha256
        ]
        if not valid:
            stale = any(a.side == side and a.choice == "approve" for a in i.approvals)
            r.append(Reason("APPROVAL_STALE" if stale else "APPROVAL_MISSING", side))
    if i.capture_id is None or i.case_capture_id != i.capture_id:
        r.append(Reason("AGREEMENT_MISMATCH", "case is not bound to this capture"))
    elif i.capture_agreement_id != i.case_agreement_id:
        r.append(Reason("AGREEMENT_MISMATCH", "capture belongs to another agreement"))
    if i.capture_status != "completed":
        r.append(Reason("CAPTURE_NOT_COMPLETED", f"capture is {i.capture_status}"))
    if i.proposal_currency != i.capture_currency:
        r.append(Reason("CURRENCY_MISMATCH"))
    amount = i.proposal_amount_minor
    if amount is None or amount <= 0:
        r.append(Reason("REFUND_AMOUNT_INVALID"))
    if any(x.status in UNCERTAIN for x in i.refunds):
        r.append(Reason("REFUND_OUTCOME_UNKNOWN", "reconcile the uncertain refund first"))
    mine = [x for x in i.refunds if x.proposal_id == i.proposal_id]
    if any(x.status in IN_FLIGHT for x in mine):
        r.append(Reason("REFUND_IN_PROGRESS"))
    if any(x.status == "completed" for x in mine) and i.proposal_status != "executed":
        r.append(Reason("REFUND_ALREADY_COMPLETED"))
    live_for_proposal = any(x.status in HOLDING for x in mine)
    if amount is not None and amount > 0 and not live_for_proposal and amount > remaining:
        r.append(Reason("REFUND_EXCEEDS_REMAINING", f"remaining {remaining}"))
    return _result(r, i, figures)


# --------------------------------------------------------------------- void


@dataclass(frozen=True)
class VoidInputs:
    actor_authorized: bool
    authorization_id: str | None
    agreement_bound: bool
    authorization_status: str | None
    capture_status: str | None  # None when no capture row exists
    void_status: str | None
    # P06 says the delivery is verified (eligible for capture approval): the buyer alone
    # may no longer cancel; an accepted, two-sided cancel_authorization proposal is needed.
    delivery_verified: bool
    agreed_cancel_proposal_id: str | None

    def sha256(self) -> str:
        return _sha(asdict(self))


def evaluate_void(i: VoidInputs) -> FinResult:
    r: list[Reason] = []
    if not i.actor_authorized:
        r.append(Reason("ACTOR_NOT_AUTHORIZED"))
    if i.authorization_id is None or not i.agreement_bound:
        r.append(Reason("AGREEMENT_MISMATCH", "authorization is not bound to an agreement"))
    if i.capture_status in ("requested", "unknown"):
        r.append(Reason("CAPTURE_IN_PROGRESS", "reconcile the capture first"))
    elif i.capture_status in ("pending", "completed"):
        r.append(Reason("ALREADY_CAPTURED", "use a refund, not a void"))
    if i.void_status == "completed":
        r.append(Reason("VOID_ALREADY_COMPLETED"))
    elif i.void_status in ("requested", "unknown"):
        r.append(Reason("VOID_IN_PROGRESS", "reconcile the void first"))
    elif i.authorization_status != "authorized":
        r.append(Reason("AUTHORIZATION_NOT_VOIDABLE", f"authorization is {i.authorization_status}"))
    if i.delivery_verified and i.agreed_cancel_proposal_id is None:
        r.append(Reason("PROVIDER_CONSENT_REQUIRED", "delivery is verified"))
    return _result(r, i, {})
