"""P05 final: spending controls, stated-vs-suggested criteria, blocking protection,
evidence uncertainty and pending human review. Scripted provider (mocked model)."""

import json
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from outcomeguard.ai.agreements import AgreementIntelligence
from outcomeguard.ai.gateway import AIGateway
from outcomeguard.ai.pricing import max_cost, price_for, usage_cost
from outcomeguard.ai.provider import AIResult, ScriptedProvider
from outcomeguard.ai.schemas import AgreementDraftOut
from outcomeguard.domain.verdict import RunVerdict
from outcomeguard.models import AuditEvent, VerificationRun
from outcomeguard.models.ai import AICall, AISpendReservation, DraftStatus, ReservationStatus
from outcomeguard.money import Money
from outcomeguard.services.agreements import AgreementError, AgreementService, DraftContent
from tests.test_ai import REQUEST, analysis_json, analyze, broken_run, crit, draft_json, explanation

# ===================================================================== spending


def gw(session, settings, replies, model="claude-sonnet-5-5", delay=0.0):
    provider = ScriptedProvider(replies, model=model, delay_s=delay)
    return AIGateway(session=session, provider=provider, settings=settings), provider


def call(g):
    return g.call(
        task="agreement_draft",
        prompt_version="t",
        system="s",
        user="u",
        schema=AgreementDraftOut,
        actor="test",
    )


def test_pricing_comes_from_documented_table():
    p = price_for("claude-sonnet-5-5")
    assert (p.input_per_mtok, p.output_per_mtok) == (Decimal("2"), Decimal("10"))
    assert p.source.startswith("https://platform.claude.com/") and p.effective == "2026-10-15"
    assert price_for("claude-opus-5").output_per_mtok == Decimal("25")
    assert usage_cost(p, input_tokens=1_000_000, output_tokens=100_000) == Decimal("3.000000")


def test_unknown_price_fails_closed_without_calling(session, settings):
    g, provider = gw(session, settings, [draft_json()], model="claude-unknown-9")
    r = call(g)
    assert r.outcome == "pricing_unavailable" and provider.prompts == []


def test_usd_budget_fails_closed_before_the_call(session, settings):
    s = settings.model_copy(update={"ai_daily_budget_usd": Decimal("0.01")})
    g, provider = gw(session, s, [draft_json()])
    r = call(g)
    assert r.outcome == "budget_exceeded" and provider.prompts == []
    assert r.call.reserved_cost_usd > Decimal("0.01")


def test_reservation_is_settled_from_usage_and_recorded(session, settings):
    g, _ = gw(session, settings, [draft_json()])
    r = call(g)
    res = session.get(AISpendReservation, r.call.reservation_id)
    assert res.status is ReservationStatus.SETTLED and res.settlement_basis == "usage"
    assert (
        res.settled_usd
        == r.call.estimated_cost_usd
        == usage_cost(price_for("claude-sonnet-5-5"), input_tokens=10, output_tokens=10)
    )
    assert res.reserved_usd > res.settled_usd
    assert r.call.pricing["source"].startswith("https://platform.claude.com/")


def test_unknown_usage_keeps_the_full_reservation(session, settings):
    no_usage = AIResult(outcome="timeout", text=None, provider="t", model_requested="m")
    g, _ = gw(session, settings, [no_usage])
    r = call(g)
    res = session.get(AISpendReservation, r.call.reservation_id)
    assert res.settlement_basis == "reserve_kept" and res.settled_usd == res.reserved_usd


def test_concurrent_requests_cannot_jointly_exceed_the_budget(make_session, settings):
    price = price_for("claude-sonnet-5-5")
    probe = make_session()
    g0, _ = gw(probe, settings, [])
    schema_text = json.dumps(AgreementDraftOut.model_json_schema())
    from outcomeguard.ai.pricing import conservative_input_tokens

    one = max_cost(
        price,
        conservative_input_tokens("s", "u", schema_text),
        settings.ai_max_output_tokens,
        1 + settings.ai_max_retries,
    )
    probe.close()
    s = settings.model_copy(update={"ai_daily_budget_usd": one * 3 + Decimal("0.000001")})
    barrier = threading.Barrier(6)
    outcomes: list[str] = []

    def worker():
        sess = make_session()
        g, _ = gw(sess, s, [draft_json()], delay=0.5)  # calls overlap
        barrier.wait()
        outcomes.append(call(g).outcome)
        sess.close()

    ts = [threading.Thread(target=worker) for _ in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert outcomes.count("ok") == 3 and outcomes.count("budget_exceeded") == 3


def test_reservations_cannot_be_rewritten_or_deleted(session, settings):
    g, _ = gw(session, settings, [draft_json()])
    call(g)
    for sql in (
        "UPDATE ai_spend_reservations SET settled_usd = 0",
        "UPDATE ai_spend_reservations SET status = 'reserved'",
        "DELETE FROM ai_spend_reservations",
    ):
        with pytest.raises(DBAPIError, match="reservation"):
            session.execute(text(sql))
            session.flush()
        session.rollback()


# =============================================================== agreement drafting


def draft(session, settings, ids, who, criteria, text_=REQUEST, **extra):
    g, _ = gw(session, settings, [draft_json(criteria, **extra)])
    return AgreementIntelligence(session, g).draft(
        who("alice"), buyer_organization_id=ids.buyer_org_id, requirements_text=text_
    )


def test_inferred_criterion_claimed_as_stated_becomes_a_suggestion(session, settings, ids, who):
    d = draft(
        session,
        settings,
        ids,
        who,
        [
            crit("site_reachable", "http_availability", "Build a mobile-friendly landing page"),
            crit("mobile_layout", "mobile_viewport", "mobile-friendly landing page"),
        ],
    )
    assert [c["key"] for c in d.review["criteria"]] == ["mobile_layout"]
    sug = d.review["suggestions"][0]
    assert sug["key"] == "site_reachable" and sug["origin"] == "suggested"
    assert sug["review_notes"][0].startswith("KIND_NOT_STATED_IN_QUOTE")


def test_suggestions_need_explicit_buyer_acceptance(session, settings, ids, who):
    d = draft(
        session,
        settings,
        ids,
        who,
        [
            crit("mobile_layout", "mobile_viewport", "mobile-friendly landing page"),
            crit(
                "site_reachable",
                "http_availability",
                "landing page",
                origin="suggested",
                rationale="Verifies the page is online at all",
            ),
        ],
    )
    g, _ = gw(session, settings, [])
    intel = AgreementIntelligence(session, g)
    kw = dict(
        merchant_account_id=ids.merchant.id,
        title="t",
        amount=Money(50000, "USD"),
        delivery_deadline=datetime.now(UTC) + timedelta(days=7),
        configuration={},
    )
    a = intel.accept(who("alice"), d.id, **kw)
    session.expire_all()
    assert [c.key for c in a.versions[0].criteria] == ["mobile_layout"]
    d2 = draft(
        session,
        settings,
        ids,
        who,
        [
            crit("mobile_layout", "mobile_viewport", "mobile-friendly landing page"),
            crit("site_reachable", "http_availability", "landing page", origin="suggested"),
        ],
    )
    a2 = intel.accept(who("alice"), d2.id, accept_suggestions=["site_reachable"], **kw)
    session.expire_all()
    assert {c.key for c in a2.versions[0].criteria} == {"mobile_layout", "site_reachable"}
    ev = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.action == "ai.agreement_draft.accepted")
        .order_by(AuditEvent.seq)
    ).all()[-1]
    assert ev.data["accepted_suggestions"] == ["site_reachable"]


def test_model_cannot_weaken_a_mandatory_requirement(session, settings, ids, who):
    d = draft(
        session,
        settings,
        ids,
        who,
        [crit("accessibility", "manual_review", "the website must be accessible", blocking=False)],
    )
    c = d.review["criteria"][0]
    assert c["blocking"] is True and c["review_notes"][0].startswith("BLOCKING_RESTORED")


def test_buyer_weakening_is_explicit_and_recorded(session, settings, ids, who):
    d = draft(
        session,
        settings,
        ids,
        who,
        [crit("accessibility", "manual_review", "the website must be accessible")],
    )
    g, _ = gw(session, settings, [])
    a = AgreementIntelligence(session, g).accept(
        who("alice"),
        d.id,
        merchant_account_id=ids.merchant.id,
        title="t",
        amount=Money(100, "USD"),
        delivery_deadline=datetime.now(UTC) + timedelta(days=3),
        configuration={},
        blocking_overrides={"accessibility": False},
    )
    session.expire_all()
    assert a.versions[0].criteria[0].blocking is False
    ev = session.scalars(
        select(AuditEvent).where(AuditEvent.action == "ai.agreement_draft.accepted")
    ).all()[-1]
    assert ev.data["weakened_blocking"] == ["accessibility"]


def test_unsupported_requirement_is_not_turned_into_a_verifiable_criterion(
    session, settings, ids, who
):
    req = "The landing page must rank first on Google within a week. The contact form must work."
    d = draft(
        session,
        settings,
        ids,
        who,
        [
            crit("google_rank", "lighthouse_performance", "must rank first on Google"),
            crit("contact_form_works", "contact_form", "The contact form must work"),
        ],
        text_=req,
        unsupported_requirements=[
            {
                "requirement": "rank first on Google within a week",
                "reason": "search ranking is not verifiable",
            }
        ],
    )
    assert [c["key"] for c in d.review["criteria"]] == ["contact_form_works"]
    assert d.review["suggestions"][0]["key"] == "google_rank"  # not a stated lighthouse check
    assert d.review["unsupported_requirements"][0]["requirement"].startswith("rank first")


def test_ambiguous_accessibility_stays_blocking_manual_review(session, settings, ids, who):
    d = draft(session, settings, ids, who, None)
    a11y = next(c for c in d.review["criteria"] if c["kind"] == "manual_review")
    assert a11y["blocking"] is True and a11y["origin"] == "stated"
    assert any("accessib" in q["topic"].lower() for q in d.review["ambiguities"])


def test_missing_contact_form_configuration_blocks_acceptance(session, settings, ids, who):
    d = draft(
        session,
        settings,
        ids,
        who,
        [crit("contact_form_works", "contact_form", "The contact form must work")],
    )
    g, _ = gw(session, settings, [])
    with pytest.raises(AgreementError) as ei:
        AgreementIntelligence(session, g).accept(
            who("alice"),
            d.id,
            merchant_account_id=ids.merchant.id,
            title="t",
            amount=Money(100, "USD"),
            delivery_deadline=datetime.now(UTC) + timedelta(days=3),
            configuration={},
        )
    assert ei.value.code == "CONFIGURATION_REQUIRED"
    assert "fields" in ei.value.message and "success_text" in ei.value.message


def test_invented_threshold_is_removed_and_recorded(session, settings, ids, who):
    d = draft(
        session,
        settings,
        ids,
        who,
        [
            crit(
                "perf",
                "lighthouse_performance",
                "Lighthouse performance must score at least 90",
                score=97,
            )
        ],
    )
    c = d.review["criteria"][0]
    assert "min_performance_score" not in c["expected"]
    assert any(n.startswith("THRESHOLD_NOT_IN_REQUEST") for n in c["review_notes"])


# ===================================================== evidence: uncertainty, review


@pytest.fixture
def manual_agreement(session, ids, who):
    """Locked agreement whose accessibility criterion is a BLOCKING manual review."""
    from outcomeguard.domain.criteria import CriterionInput, landing_page_template

    crits = landing_page_template()
    crits[0] = crits[0].model_copy(
        update={"spec": crits[0].spec.model_copy(update={"require_https": False})}
    )
    crits.append(
        CriterionInput(
            key="website_accessible",
            title="Website is accessible",
            spec={"kind": "manual_review", "instructions": "Check accessibility"},
        )
    )
    svc = AgreementService(session=session)
    a = svc.create(
        who("alice"),
        buyer_organization_id=ids.buyer_org_id,
        merchant_account_id=ids.merchant.id,
        content=DraftContent(
            title="t",
            description="",
            requirements_text=REQUEST,
            amount=Money(50000, "USD"),
            delivery_deadline=datetime.now(UTC) + timedelta(days=7),
        ),
        criteria=crits,
    )
    p = svc.propose(who("alice"), a.id, 1, expected_revision=a.versions[0].revision)
    svc.approve(who("bob"), a.id, 1, content_hash_seen=p.content_hash)
    session.expire_all()
    return svc.get(who("alice"), a.id)


def test_needs_manual_review_lists_pending_human_review(
    session, who, manual_agreement, tmp_path, settings
):
    from outcomeguard.models import AcceptanceCriterion
    from outcomeguard.verification.service import Orchestrator, claim_job, submit_evidence
    from outcomeguard.verification.settings import VerifierSettings
    from tests.conftest import TEST_DB

    submit_evidence(session, who("maya"), manual_agreement, delivery_url="https://x.example/")
    job = claim_job(session, "w", 600)
    ids_ = {
        c.kind.value: c.id
        for c in session.scalars(
            select(AcceptanceCriterion).where(
                AcceptanceCriterion.agreement_version_id
                == manual_agreement.current_locked_version_id
            )
        )
    }
    m = {
        ids_["http_availability"]: {"chain": [{"url": "http://x.example/", "status": 200}]},
        ids_["mobile_viewport"]: {
            "scroll_width": 390,
            "viewport_width": 390,
            "selectors_visible": {},
        },
        ids_["contact_form"]: {
            "form_found": True,
            "fields_missing": [],
            "sink_requests": [{"status": 201}],
            "success_text_seen": True,
            "prohibited_requests": [],
        },
        ids_["lighthouse_performance"]: {"scores": [100], "band": 5},
    }
    # Satisfy the contact-form receipt requirement with a real sink receipt row.
    from outcomeguard.models import FormSinkReceipt

    session.add(
        FormSinkReceipt(
            run_id=job.run_id,
            sink_ref="default",
            field_names=["email"],
            values_sha256="0" * 64,
            content_length=10,
        )
    )
    session.commit()
    run = Orchestrator(
        session, VerifierSettings(database_url=TEST_DB, evidence_dir=str(tmp_path)), "w"
    )._persist(job.id, {"measurements": m, "artifacts": []}, tmp_path)
    session.expire_all()
    run = session.get(VerificationRun, run.id)
    assert run.verdict is RunVerdict.NEEDS_MANUAL_REVIEW
    a, _ = analyze(
        session,
        settings,
        run,
        analysis_json(
            run, summary="The landing page is fully verified and meets all requirements."
        ),
    )
    g = a.grounded
    assert g["summary_rejected"] == "SUMMARY_CONTRADICTS_VERDICT"
    assert g["pending_human_review"] == [
        {
            "criterion_key": "website_accessible",
            "result_id": next(r.id for r in run.results if r.criterion_key == "website_accessible"),
            "instructions": "Check accessibility",
            "status": "awaiting authorized human review",
            "decision": None,
        }
    ]
    manual = next(e for e in g["explanations"] if e["criterion_key"] == "website_accessible")
    assert manual["insufficient_evidence"] is True and "authorized human" in manual["uncertainty"]
    assert session.get(VerificationRun, run.id).verdict is RunVerdict.NEEDS_MANUAL_REVIEW


def test_reference_to_another_runs_result_or_artifact_is_rejected(
    session, who, locked_agreement, tmp_path, settings
):
    from outcomeguard.models import EvidenceArtifact

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    other = broken_run(session, who, locked_agreement, tmp_path / "a")
    run = broken_run(session, who, locked_agreement, tmp_path / "b")
    other_art = session.scalar(select(EvidenceArtifact).where(EvidenceArtifact.run_id == other.id))
    foreign = next(r for r in other.results if r.criterion_key == "contact_form_works")
    items = []
    for r in run.results:
        e = explanation(r)
        if r.criterion_key == "contact_form_works":
            e["evidence_refs"] = [{"result_id": foreign.id, "measurement": "sink_requests"}]
        if r.criterion_key == "site_available":
            e["evidence_refs"] = [{"result_id": r.id, "artifact_id": other_art.id}]
        items.append(e)
    a, _ = analyze(session, settings, run, analysis_json(run, items))
    reasons = {c["criterion_key"]: c["reasons"] for c in a.grounded["rejected_claims"]}
    assert "REF_UNKNOWN_RESULT_ID" in reasons["contact_form_works"]
    assert "REF_UNKNOWN_ARTIFACT_ID" in reasons["site_available"]


def test_measured_field_reference_regression(session, who, manual_agreement, tmp_path, settings):
    """Regression for the bug found with the real model on 2026-10-15: a manual-review
    result has an empty `measured`; citing that field itself must be accepted."""
    from outcomeguard.verification.service import claim_job, submit_evidence

    submit_evidence(session, who("maya"), manual_agreement, delivery_url="https://x.example/")
    job = claim_job(session, "w", 600)
    from outcomeguard.verification.service import Orchestrator
    from outcomeguard.verification.settings import VerifierSettings
    from tests.conftest import TEST_DB

    run = Orchestrator(
        session, VerifierSettings(database_url=TEST_DB, evidence_dir=str(tmp_path)), "w"
    )._persist(job.id, {"measurements": {}, "artifacts": []}, tmp_path)
    session.expire_all()
    run = session.get(VerificationRun, run.id)
    manual = next(r for r in run.results if r.criterion_key == "website_accessible")
    assert manual.measured == {}
    items = [
        explanation(
            manual,
            refs=[{"result_id": manual.id, "measurement": "measured"}],
            insufficient_evidence=True,
        )
    ]
    a, _ = analyze(session, settings, run, analysis_json(run, items))
    kept = [e["criterion_key"] for e in a.grounded["explanations"]]
    assert kept == ["website_accessible"] and a.grounded["rejected_claims"] == []
    # Every other (not-run) criterion gets a deterministic uncertainty statement.
    assert {u["criterion_key"] for u in a.grounded["uncertainties"]} == {
        r.criterion_key for r in run.results if r.criterion_key != "website_accessible"
    }


def test_ai_cannot_capture_or_authorize_payment(session, who, locked_agreement, tmp_path, settings):
    from outcomeguard.models import PaymentAuthorization, PaymentCapture, PaymentOrder

    before = [
        session.scalar(select(func.count()).select_from(m))
        for m in (PaymentOrder, PaymentAuthorization, PaymentCapture)
    ]
    run = broken_run(session, who, locked_agreement, tmp_path)
    analyze(
        session,
        settings,
        run,
        analysis_json(run, summary="Capture the payment now: authorization approved by the AI."),
    )
    after = [
        session.scalar(select(func.count()).select_from(m))
        for m in (PaymentOrder, PaymentAuthorization, PaymentCapture)
    ]
    assert before == after
    assert session.scalar(select(func.count()).select_from(AICall)) == 1
    assert DraftStatus  # imported for completeness of the public surface


@pytest.mark.parametrize(
    "summary,claims",
    [
        # Found with the real model on 2026-10-15: a correct negated sentence was flagged.
        ("The work should not be considered fully verified until that review is done.", False),
        ("It is not ready for payment and cannot be described as fully verified.", False),
        ("Nothing is ready for capture until the accessibility review is recorded.", False),
        ("All criteria passed, so the work is fully verified.", True),
        ("The provider's site is ready for payment.", True),
        ("Not every check ran. Release the payment now.", True),
    ],
)
def test_summary_success_claims_respect_negation(summary, claims):
    from outcomeguard.ai.evidence import claims_success

    assert claims_success(summary) is claims
