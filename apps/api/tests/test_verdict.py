"""Deterministic verdict engine: outcome rules and 'never PASS on uncertainty'."""

import itertools

import pytest

from outcomeguard.domain.verdict import (
    CriterionOutcome as O,
)
from outcomeguard.domain.verdict import (
    RunVerdict as V,
)
from outcomeguard.domain.verdict import (
    evaluate,
    run_verdict,
)

HTTP = {"expected_status": [200], "max_redirects": 2, "require_https": True}
FORM = {"fields": [{"selector": "input[name=email]", "value": "verifier@example.com"}]}
LH = {"min_score": 90}


def test_http_rules():
    ok = {"chain": [{"url": "https://a.example/", "status": 200}]}
    assert evaluate("http_availability", HTTP, ok).outcome is O.PASS
    assert (
        evaluate(
            "http_availability", HTTP, {"chain": [{"url": "http://a.example/", "status": 200}]}
        ).reason_code
        == "NOT_HTTPS"
    )
    assert (
        evaluate(
            "http_availability", HTTP, {"chain": [{"url": "https://a/", "status": 500}]}
        ).outcome
        is O.FAIL
    )
    many = {
        "chain": [{"url": "https://a/", "status": 301}] * 3 + [{"url": "https://a/", "status": 200}]
    }
    assert evaluate("http_availability", HTTP, many).reason_code == "TOO_MANY_REDIRECTS"
    assert (
        evaluate(
            "http_availability", HTTP, {"error": "x", "error_code": "BLOCKED_BY_EGRESS_POLICY"}
        ).outcome
        is O.ERROR
    )


def test_mobile_rules():
    spec = {"no_horizontal_overflow": True, "required_visible_selectors": ["form"]}
    assert (
        evaluate(
            "mobile_viewport",
            spec,
            {"scroll_width": 390, "viewport_width": 390, "selectors_visible": {"form": True}},
        ).outcome
        is O.PASS
    )
    assert (
        evaluate(
            "mobile_viewport",
            spec,
            {"scroll_width": 600, "viewport_width": 390, "selectors_visible": {"form": True}},
        ).reason_code
        == "HORIZONTAL_OVERFLOW"
    )
    assert (
        evaluate(
            "mobile_viewport",
            spec,
            {"scroll_width": 390, "viewport_width": 390, "selectors_visible": {"form": False}},
        ).outcome
        is O.FAIL
    )
    assert evaluate("mobile_viewport", spec, {}).outcome is O.ERROR


@pytest.mark.parametrize(
    "m,receipts,outcome,code",
    [
        (
            {
                "form_found": True,
                "fields_missing": [],
                "sink_requests": [{"status": 201}],
                "success_text_seen": True,
            },
            1,
            O.PASS,
            "OK",
        ),
        (
            {
                "form_found": True,
                "fields_missing": [],
                "sink_requests": [],
                "success_text_seen": False,
            },
            0,
            O.FAIL,
            "NO_SUBMISSION_TO_SINK",
        ),
        # The page claims success but nothing reached the sink → still FAIL.
        (
            {
                "form_found": True,
                "fields_missing": [],
                "sink_requests": [],
                "success_text_seen": True,
            },
            0,
            O.FAIL,
            "NO_SUBMISSION_TO_SINK",
        ),
        # Browser saw 201 but the DB has no receipt → FAIL (runner output can't fake it).
        (
            {
                "form_found": True,
                "fields_missing": [],
                "sink_requests": [{"status": 201}],
                "success_text_seen": True,
            },
            0,
            O.FAIL,
            "NO_SINK_RECEIPT",
        ),
        (
            {
                "form_found": True,
                "fields_missing": [],
                "sink_requests": [{"status": 422}],
                "success_text_seen": False,
            },
            0,
            O.FAIL,
            "SINK_REJECTED_SUBMISSION",
        ),
        (
            {
                "prohibited_requests": [{"method": "POST", "url": "https://evil/"}],
                "success_text_seen": True,
                "sink_requests": [{"status": 201}],
            },
            1,
            O.FAIL,
            "FORM_POSTS_TO_UNAPPROVED_DESTINATION",
        ),
        (
            {"prohibited_requests": [{"method": "POST", "url": "https://evil/"}], "error": "x"},
            0,
            O.FAIL,
            "FORM_POSTS_TO_UNAPPROVED_DESTINATION",
        ),
        ({"unverifiable": "FORM_IN_SUBFRAME"}, 0, O.MANUAL_REVIEW_REQUIRED, "FORM_IN_SUBFRAME"),
        ({"form_found": False}, 0, O.FAIL, "FORM_NOT_FOUND"),
        ({"error": "nav", "error_code": "NAVIGATION_FAILED"}, 0, O.ERROR, "NAVIGATION_FAILED"),
    ],
)
def test_contact_form_rules(m, receipts, outcome, code):
    ev = evaluate("contact_form", FORM, m, sink_receipts=receipts)
    assert (ev.outcome, ev.reason_code) == (outcome, code)


def test_lighthouse_median_and_adaptive_protocol():
    assert evaluate("lighthouse_performance", LH, {"scores": [100], "band": 5}).outcome is O.PASS
    assert evaluate("lighthouse_performance", LH, {"scores": [40], "band": 5}).outcome is O.FAIL
    # Near threshold, one run is not enough → ERROR, never a guess.
    assert (
        evaluate("lighthouse_performance", LH, {"scores": [92], "band": 5}).reason_code
        == "INSUFFICIENT_RUNS_NEAR_THRESHOLD"
    )
    assert (
        evaluate("lighthouse_performance", LH, {"scores": [92, 85, 88], "band": 5}).reason_code
        == "SCORE_BELOW_THRESHOLD"
    )
    assert (
        evaluate("lighthouse_performance", LH, {"scores": [92, 89, 91], "band": 5}).outcome
        is O.PASS
    )


def test_missing_measurement_is_not_run():
    assert evaluate("http_availability", HTTP, None).outcome is O.NOT_RUN
    assert evaluate("manual_review", {}, None).outcome is O.MANUAL_REVIEW_REQUIRED


def test_run_verdict_never_passes_on_anything_but_all_pass():
    for combo in itertools.product(list(O), repeat=3):
        v = run_verdict([(True, o) for o in combo])
        if all(o is O.PASS for o in combo):
            assert v is V.PASS
        else:
            assert v is not V.PASS
        if O.FAIL in combo:
            assert v is V.FAIL


def test_non_blocking_failures_do_not_change_verdict_and_empty_is_inconclusive():
    assert run_verdict([(True, O.PASS), (False, O.FAIL)]) is V.PASS
    assert run_verdict([]) is V.INCONCLUSIVE
    assert run_verdict([(False, O.PASS)]) is V.INCONCLUSIVE
    assert run_verdict([(True, O.PASS), (True, O.MANUAL_REVIEW_REQUIRED)]) is V.NEEDS_MANUAL_REVIEW
