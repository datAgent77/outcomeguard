"""Pure P08 policy: refund and void reason codes, and the case state machine (no DB)."""

from dataclasses import replace

import pytest

from outcomeguard.domain.resolution_policy import (
    TERMINAL,
    TRANSITIONS,
    ApprovalIn,
    FinDecision,
    RefundIn,
    RefundInputs,
    TransitionError,
    VoidInputs,
    evaluate_refund,
    evaluate_void,
    next_state,
)

H = "h" * 64


def ok_approvals(h=H):
    return (
        ApprovalIn("buyer", "approve", h, True, True),
        ApprovalIn("provider", "approve", h, True, True),
    )


def base(**kw) -> RefundInputs:
    i = RefundInputs(
        actor_authorized=True,
        case_id="c",
        case_status="agreed",
        case_agreement_id="ag",
        case_capture_id="cap",
        proposal_id="p",
        proposal_case_id="c",
        proposal_status="accepted",
        proposal_remedy="partial_refund",
        proposal_amount_minor=10000,
        proposal_currency="USD",
        proposal_content_sha256=H,
        approvals=ok_approvals(),
        capture_id="cap",
        capture_status="completed",
        capture_agreement_id="ag",
        capture_amount_minor=50000,
        capture_currency="USD",
        refunds=(),
    )
    return replace(i, **kw)


def codes(r):
    return [x.code for x in r.reasons]


def test_agreed_two_sided_refund_within_balance_is_allowed():
    r = evaluate_refund(base())
    assert r.decision is FinDecision.ALLOWED and r.reasons == ()
    assert r.figures == {
        "captured_minor": 50000,
        "refunded_minor": 0,
        "held_minor": 0,
        "remaining_minor": 50000,
        "requested_minor": 10000,
    }


@pytest.mark.parametrize(
    "change,code",
    [
        ({"actor_authorized": False}, "ACTOR_NOT_AUTHORIZED"),
        ({"case_id": None}, "RESOLUTION_NOT_FOUND"),
        ({"case_status": "proposed"}, "RESOLUTION_NOT_AGREED"),
        ({"proposal_status": "open"}, "RESOLUTION_NOT_AGREED"),
        ({"proposal_case_id": "other"}, "RESOLUTION_NOT_AGREED"),
        ({"approvals": (ApprovalIn("buyer", "approve", H, True, True),)}, "APPROVAL_MISSING"),
        ({"approvals": ok_approvals("x" * 64)}, "APPROVAL_STALE"),
        (
            {
                "approvals": (
                    ApprovalIn("buyer", "approve", H, True, True),
                    ApprovalIn("provider", "approve", H, False, True),  # service account
                )
            },
            "APPROVAL_STALE",
        ),
        (
            {
                "approvals": (
                    ApprovalIn("buyer", "approve", H, True, True),
                    ApprovalIn("provider", "approve", H, True, False),  # forged side
                )
            },
            "APPROVAL_STALE",
        ),
        ({"proposal_amount_minor": 0}, "REFUND_AMOUNT_INVALID"),
        ({"proposal_remedy": "rework"}, "REFUND_AMOUNT_INVALID"),
        ({"proposal_amount_minor": 50001}, "REFUND_EXCEEDS_REMAINING"),
        ({"capture_status": "unknown"}, "CAPTURE_NOT_COMPLETED"),
        ({"proposal_currency": "EUR"}, "CURRENCY_MISMATCH"),
        ({"case_capture_id": "other-cap"}, "AGREEMENT_MISMATCH"),
        ({"capture_agreement_id": "other-ag"}, "AGREEMENT_MISMATCH"),
        ({"proposal_status": "executed"}, "REFUND_ALREADY_COMPLETED"),
        ({"refunds": (RefundIn("unknown", 1000, "USD", "older"),)}, "REFUND_OUTCOME_UNKNOWN"),
        (
            {"refunds": (RefundIn("reconciliation_required", 1000, "USD", "older"),)},
            "REFUND_OUTCOME_UNKNOWN",
        ),
        ({"refunds": (RefundIn("requested", 10000, "USD", "p"),)}, "REFUND_IN_PROGRESS"),
        ({"refunds": (RefundIn("completed", 10000, "USD", "p"),)}, "REFUND_ALREADY_COMPLETED"),
    ],
)
def test_each_refund_condition_blocks(change, code):
    r = evaluate_refund(base(**change))
    assert r.decision is FinDecision.BLOCKED and code in codes(r)


def test_multiple_partial_refunds_respect_the_remaining_balance():
    done = (
        RefundIn("completed", 20000, "USD", "p1"),
        RefundIn("completed", 20000, "USD", "p2"),
    )
    assert (
        evaluate_refund(base(refunds=done, proposal_amount_minor=10000)).decision
        is FinDecision.ALLOWED
    )
    r = evaluate_refund(base(refunds=done, proposal_amount_minor=10001))
    assert codes(r) == ["REFUND_EXCEEDS_REMAINING"] and r.figures["remaining_minor"] == 10000


def test_failed_refunds_do_not_hold_balance_but_pending_does():
    failed = (RefundIn("failed", 50000, "USD", "p0"),)
    assert (
        evaluate_refund(base(refunds=failed, proposal_amount_minor=50000)).decision
        is FinDecision.ALLOWED
    )
    pending = (RefundIn("pending", 45000, "USD", "p0"),)
    r = evaluate_refund(base(refunds=pending, proposal_amount_minor=10000))
    assert "REFUND_EXCEEDS_REMAINING" in codes(r)


def test_inputs_hash_tracks_facts():
    assert base().sha256() == base().sha256()
    assert base(proposal_amount_minor=9999).sha256() != base().sha256()
    assert evaluate_refund(base()).inputs_sha256 == base().sha256()


# ---------------------------------------------------------------------- void


def vbase(**kw) -> VoidInputs:
    return replace(
        VoidInputs(
            actor_authorized=True,
            authorization_id="a",
            agreement_bound=True,
            authorization_status="authorized",
            capture_status=None,
            void_status=None,
            delivery_verified=False,
            agreed_cancel_proposal_id=None,
        ),
        **kw,
    )


def test_uncaptured_authorization_can_be_voided_by_buyer():
    assert evaluate_void(vbase()).decision is FinDecision.ALLOWED


@pytest.mark.parametrize(
    "change,code",
    [
        ({"actor_authorized": False}, "ACTOR_NOT_AUTHORIZED"),
        ({"capture_status": "requested"}, "CAPTURE_IN_PROGRESS"),
        ({"capture_status": "unknown"}, "CAPTURE_IN_PROGRESS"),
        ({"capture_status": "completed"}, "ALREADY_CAPTURED"),
        ({"capture_status": "pending"}, "ALREADY_CAPTURED"),
        ({"void_status": "unknown"}, "VOID_IN_PROGRESS"),
        ({"void_status": "completed"}, "VOID_ALREADY_COMPLETED"),
        ({"authorization_status": "capture_in_progress"}, "AUTHORIZATION_NOT_VOIDABLE"),
        ({"authorization_status": "expired"}, "AUTHORIZATION_NOT_VOIDABLE"),
        ({"agreement_bound": False}, "AGREEMENT_MISMATCH"),
        ({"delivery_verified": True}, "PROVIDER_CONSENT_REQUIRED"),
    ],
)
def test_each_void_condition_blocks(change, code):
    r = evaluate_void(vbase(**change))
    assert r.decision is FinDecision.BLOCKED and code in codes(r)


def test_verified_delivery_can_be_voided_with_agreed_cancellation():
    r = evaluate_void(vbase(delivery_verified=True, agreed_cancel_proposal_id="p"))
    assert r.decision is FinDecision.ALLOWED


def test_failed_void_can_be_retried():
    assert evaluate_void(vbase(void_status="failed")).decision is FinDecision.ALLOWED


# ------------------------------------------------------------ state machine


def test_happy_paths():
    s = "open"
    for ev in (
        "counterparty_activity",
        "proposal_submitted",
        "proposal_accepted",
        "execution_reserved",
        "execution_completed",
    ):
        s = next_state(s, ev)
    assert s == "resolved"
    s = next_state(
        next_state(next_state("open", "proposal_submitted"), "proposal_rejected"),
        "proposal_submitted",
    )
    assert s == "proposed"
    assert next_state("executing", "execution_uncertain") == "action_required"
    assert next_state("action_required", "execution_completed") == "resolved"


def test_terminal_states_accept_no_events():
    for state in TERMINAL:
        for ev in TRANSITIONS:
            with pytest.raises(TransitionError):
                next_state(state, ev)


@pytest.mark.parametrize(
    "state,event",
    [
        ("open", "execution_reserved"),  # no money before agreement
        ("proposed", "execution_reserved"),
        ("executing", "proposal_submitted"),  # cannot renegotiate mid-operation
        ("executing", "withdrawn_by_buyer"),
        ("open", "proposal_accepted"),
    ],
)
def test_invalid_transitions_raise(state, event):
    with pytest.raises(TransitionError):
        next_state(state, event)
