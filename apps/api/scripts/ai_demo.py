"""P05 canonical demonstration — real model, real verification, real fixtures. No payment.

1. Buyer writes a natural-language $500 landing-page agreement.
2. The AI proposes structured acceptance criteria (validated + deterministically reviewed).
3. Buyer reviews: supplies configuration the AI may not invent, explicitly accepts or
   ignores AI suggestions, keeps every stated requirement's blocking status.
4. The agreement version is locked (propose + buyer-approver approval, P03).
5. Real P04 tools verify the broken page → FAIL; the AI explains the evidence.
6. Real P04 tools verify the fixed page: automated checks pass, the blocking
   accessibility criterion is manual review → NEEDS_MANUAL_REVIEW; the AI explains.
7. The pending authorized-human-review requirement is shown. No human approval is
   fabricated, the verdict is not changed, no payment action is taken.

Prerequisites: ANTHROPIC_API_KEY in .env, fixtures on 8020, API on 8010 (docs/VERIFICATION.md).
Human review decisions in step 3 are scripted here and recorded as such.

    uv run python scripts/ai_demo.py
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from outcomeguard.ai.agreements import AgreementIntelligence
from outcomeguard.ai.evidence import EvidenceIntelligence
from outcomeguard.ai.gateway import AIGateway, build_provider
from outcomeguard.auth import load_principal
from outcomeguard.config import Settings
from outcomeguard.models import PaymentEnvironment, VerificationRun
from outcomeguard.models.ai import AICall, DraftStatus
from outcomeguard.money import Money
from outcomeguard.services.agreements import AgreementService
from outcomeguard.services.identities import ensure_demo_identities
from outcomeguard.verification.service import (
    Orchestrator,
    claim_job,
    is_verified_pass,
    new_worker_id,
    submit_evidence,
)
from outcomeguard.verification.settings import VerifierSettings

REPO = Path(__file__).resolve().parents[3]
REQUEST = (
    "Build a mobile-friendly landing page for $500. The contact form must work, the website "
    "must be accessible and Lighthouse performance must score at least 90."
)
FIXTURES = "http://127.0.0.1:8020"


def call_meta(session, call_id: str | None) -> dict:
    c = session.get(AICall, call_id) if call_id else None
    if c is None:
        return {}
    return {
        "ai_call_id": c.id,
        "provider": c.provider,
        "model_requested": c.model_requested,
        "model_used": c.model_used,
        "outcome": c.outcome,
        "latency_ms": c.latency_ms,
        "input_tokens": c.input_tokens,
        "output_tokens": c.output_tokens,
        "estimated_cost_usd": str(c.estimated_cost_usd) if c.estimated_cost_usd else None,
        "reserved_cost_usd": str(c.reserved_cost_usd) if c.reserved_cost_usd else None,
        "request_id": c.request_id,
        "prompt_version": c.prompt_version,
    }


def banner(n: int, title: str) -> None:
    print(f"\n=== {n}. {title} ===")


def main() -> int:
    settings = Settings()
    provider = build_provider(settings)
    if provider is None:
        print("ANTHROPIC_API_KEY is not configured in .env — no real model call possible.")
        return 2
    session = sessionmaker(bind=create_engine(settings.database_url), expire_on_commit=False)()
    gw = AIGateway(session=session, provider=provider, settings=settings)
    ids = ensure_demo_identities(session, PaymentEnvironment.SANDBOX)
    alice = load_principal(session, ids.users["alice@acme.demo"])
    bob = load_principal(session, ids.users["bob@acme.demo"])
    maya = load_principal(session, ids.users["maya@northwind.demo"])
    t: dict = {
        "kind": "p05-ai-demo-canonical",
        "scenario": "canonical: accessibility remains a BLOCKING manual-review criterion",
        "model_config": {
            "model": settings.ai_model,
            "effort": settings.ai_effort,
            "server_fallback": settings.ai_server_fallback,
            "max_output_tokens": settings.ai_max_output_tokens,
            "max_retries": settings.ai_max_retries,
            "daily_budget_usd": str(settings.ai_daily_budget_usd),
        },
        "started_at": datetime.now(UTC).isoformat(),
        "request": REQUEST,
        "steps": {},
    }

    banner(1, "Buyer request (natural language)")
    print(REQUEST)

    banner(2, "AI proposes acceptance criteria")
    intel = AgreementIntelligence(session, gw)
    draft = intel.draft(alice, buyer_organization_id=ids.buyer_org_id, requirements_text=REQUEST)
    meta = call_meta(session, draft.ai_call_id)
    print(json.dumps(meta, indent=1))
    if draft.status is not DraftStatus.PROPOSED:
        print("AI draft failed:", draft.review)
        return 1
    for label, items in (
        ("STATED", draft.review["criteria"]),
        ("SUGGESTED", draft.review.get("suggestions", [])),
    ):
        for c in items:
            print(
                f"  {label} [{c['kind']}] {c['key']} blocking={c['blocking']} "
                f"expected={c['expected']}\n      quote: {c['source_quote']!r}"
                f"\n      notes: {c['review_notes']}"
            )
    print("  rejected:", draft.review["rejected_criteria"])
    print("  ambiguities:", [a["topic"] for a in draft.review["ambiguities"]])
    t["steps"]["2_ai_draft"] = {
        "ai_call": meta,
        "ai_output": draft.ai_output,
        "deterministic_review": draft.review,
    }

    banner(3, "Buyer review and configuration (human decisions, scripted)")
    stated = {c["key"]: c for c in draft.review["criteria"]}
    suggestions = {c["key"]: c for c in draft.review.get("suggestions", [])}
    accepted = [k for k, c in suggestions.items() if c["kind"] == "http_availability"]
    chosen = {**stated, **{k: suggestions[k] for k in accepted}}
    decisions = {
        "use_standard_form_fields": True,  # buyer opts into the standard test-form fields
        "accept_suggestions": accepted,  # explicit acceptance of the AI's availability check
        "configuration": {  # local fixtures are http:// — buyer relaxes HTTPS for this demo
            k: {"require_https": False}
            for k, c in chosen.items()
            if c["kind"] == "http_availability"
        },
        "blocking_overrides": {},  # nothing weakened
    }
    print("buyer decisions:", decisions)
    agreement = intel.accept(
        alice,
        draft.id,
        merchant_account_id=ids.merchant.id,
        title="Spring campaign landing page (AI-drafted)",
        amount=Money(50000, "USD"),
        delivery_deadline=datetime.now(UTC) + timedelta(days=14),
        **decisions,
    )
    t["steps"]["3_buyer_review"] = {"decisions": decisions, "agreement_id": agreement.id}

    banner(4, "Lock the agreement version (propose + buyer-approver approval)")
    svc = AgreementService(session=session)
    v = agreement.versions[0]
    for c in v.criteria:
        print(f"  {c.key:<28} {c.kind.value:<24} blocking={c.blocking} source={c.source.value}")
    p = svc.propose(alice, agreement.id, 1, expected_revision=v.revision)
    svc.approve(bob, agreement.id, 1, content_hash_seen=p.content_hash)
    session.expire_all()
    agreement = svc.get(alice, agreement.id)
    print(
        f"agreement {agreement.id} status={agreement.status.value} "
        f"locked_version={agreement.current_locked_version_id} hash={p.content_hash[:16]}…"
    )
    t["steps"]["4_locked"] = {
        "agreement_version_id": agreement.current_locked_version_id,
        "content_hash": p.content_hash,
        "approved_by": "bob@acme.demo (buyer_approver)",
    }

    vs = VerifierSettings()
    worker = new_worker_id()
    evidence = EvidenceIntelligence(session, gw)
    pending: list = []
    for step, variant in ((5, "landing-page-broken"), (6, "landing-page-fixed")):
        banner(step, f"Real verification + AI explanation: {variant}")
        run = submit_evidence(session, maya, agreement, delivery_url=f"{FIXTURES}/{variant}/")
        job = claim_job(session, worker, vs.lease_seconds)
        t0 = time.monotonic()
        Orchestrator(session, vs, worker).process(job)
        session.expire_all()
        run = session.get(VerificationRun, run.id)
        results = [
            (r.criterion_key, r.blocking, r.outcome.value, r.reason_code) for r in run.results
        ]
        print(
            f"verdict={run.verdict.value} verified_pass={is_verified_pass(session, run.id)} "
            f"({time.monotonic() - t0:.1f}s)"
        )
        for r in results:
            print("  ", r)
        a = evidence.analyze(run.id, actor="user:bob@acme.demo")
        meta = call_meta(session, a.ai_call_id)
        print(json.dumps(meta, indent=1))
        print(f"analysis={a.status.value}  verdict(deterministic)={a.run_verdict}")
        print("summary:", a.grounded["summary"])
        for e in a.grounded["explanations"]:
            print(
                f"  - {e['criterion_key']} [{e['outcome']}/{e['reason_code']}] "
                f"confidence={e['confidence']}\n    {e['explanation']}\n"
                f"    refs={e['evidence_refs']}\n    remediation={e['remediation']}"
                + (f"\n    uncertainty={e['uncertainty']}" if e.get("uncertainty") else "")
            )
        print("  rejected claims:", a.grounded.get("rejected_claims"))
        print("  uncertainties:", a.grounded.get("uncertainties"))
        pending = a.grounded.get("pending_human_review", [])
        t["steps"][f"{step}_{variant}"] = {
            "run_id": run.id,
            "verdict": run.verdict.value,
            "verified_pass": is_verified_pass(session, run.id),
            "results": results,
            "ai_call": meta,
            "analysis_status": a.status.value,
            "grounded": a.grounded,
            "ai_output": a.ai_output,
        }

    banner(7, "Pending authorized human review (no approval fabricated)")
    for p_ in pending:
        print(f"  {p_['criterion_key']}: {p_['status']} — {p_['instructions']}")
    print("  The fixed page is NOT fully verified: verdict stays NEEDS_MANUAL_REVIEW,")
    print("  is_verified_pass = False, no payment action. P06 will record an authorized")
    print("  reviewer decision bound to version, criterion, evidence, reviewer and time.")
    t["steps"]["7_pending_human_review"] = {
        "pending": pending,
        "human_decisions": [],
        "payment_actions": [],
    }

    out = (
        REPO
        / "docs"
        / "integration"
        / "runs"
        / (f"ai-demo-canonical-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json")
    )
    out.write_text(json.dumps(t, indent=2, default=str))
    print(f"\ntranscript: {out.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
