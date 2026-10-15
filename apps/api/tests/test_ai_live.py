"""Opt-in tests against the REAL model API (costs money; needs ANTHROPIC_API_KEY in .env).

uv run pytest -m ai_live -s
"""

import pytest

from outcomeguard.ai.agreements import AgreementIntelligence
from outcomeguard.ai.evidence import EvidenceIntelligence
from outcomeguard.ai.gateway import AIGateway, build_provider
from outcomeguard.config import Settings
from outcomeguard.domain.verdict import RunVerdict
from outcomeguard.models import VerificationRun
from outcomeguard.models.ai import AICall, AnalysisStatus, DraftStatus
from tests.test_ai import INJECTION, REQUEST, broken_run

pytestmark = pytest.mark.ai_live


@pytest.fixture
def live_gateway(session):
    s = Settings()
    provider = build_provider(s)
    if provider is None:
        pytest.skip("ANTHROPIC_API_KEY not configured")
    return AIGateway(session=session, provider=provider, settings=s)


def test_real_model_drafts_grounded_supported_criteria(session, ids, who, live_gateway):
    d = AgreementIntelligence(session, live_gateway).draft(
        who("alice"), buyer_organization_id=ids.buyer_org_id, requirements_text=REQUEST
    )
    call = session.get(AICall, d.ai_call_id)
    print(
        f"\nmodel={call.model_used} latency={call.latency_ms}ms tokens in/out="
        f"{call.input_tokens}/{call.output_tokens}"
    )
    assert call.outcome == "ok" and d.status is DraftStatus.PROPOSED, call.error
    # Reproducible proof: no server-side fallback, the requested model answered.
    assert live_gateway.settings.ai_server_fallback is False
    assert call.model_used == call.model_requested == live_gateway.settings.ai_model
    assert call.estimated_cost_usd is not None and call.estimated_cost_usd <= call.reserved_cost_usd
    print(f"estimated cost ${call.estimated_cost_usd} (reserved ${call.reserved_cost_usd})")
    kept = d.review["criteria"]
    print("stated:", [(c["key"], c["kind"], c["blocking"], c["expected"]) for c in kept])
    print("suggested:", [(c["key"], c["kind"]) for c in d.review.get("suggestions", [])])
    print("rejected:", d.review["rejected_criteria"])
    kinds = {c["kind"] for c in kept}
    assert {"contact_form", "lighthouse_performance"} <= kinds
    lh = next(c for c in kept if c["kind"] == "lighthouse_performance")
    assert lh["expected"].get("min_performance_score") == 90
    # "accessible" has no automated check: must not be silently dropped.
    a11y = [
        c
        for c in kept
        if c["kind"] == "manual_review" and "accessib" in (c["source_quote"] + c["title"]).lower()
    ]
    assert a11y and a11y[0]["blocking"] is True, "accessibility must stay a blocking review"


def test_real_model_resists_injection_and_cannot_change_outcomes(
    session, who, locked_agreement, tmp_path, live_gateway
):
    run = broken_run(session, who, locked_agreement, tmp_path, injection=INJECTION)
    a = EvidenceIntelligence(session, live_gateway).analyze(run.id, actor="live-test")
    call = session.get(AICall, a.ai_call_id)
    print(f"\nmodel={call.model_used} latency={call.latency_ms}ms status={a.status.value}")
    print("summary:", a.grounded["summary"][:400])
    print("rejected:", a.grounded.get("rejected_claims"))
    assert a.status is not AnalysisStatus.AI_UNAVAILABLE, call.error
    assert call.model_used == call.model_requested
    form = [e for e in a.grounded["explanations"] if e["criterion_key"] == "contact_form_works"]
    assert all(e["outcome"] == "fail" for e in form)
    session.expire_all()
    assert session.get(VerificationRun, run.id).verdict is RunVerdict.FAIL
