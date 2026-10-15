"""P05 demonstration — real model, real verification, real fixtures. No payment.

1. Buyer writes a natural-language agreement.
2. The AI proposes structured acceptance criteria (validated + deterministically reviewed).
3. Buyer reviews: supplies configuration the AI may not invent, decides blocking, accepts;
   then proposes and a buyer approver locks the version (P03).
4. Real P04 tools verify the broken page → FAIL.
5. The AI explains the failure, bound to the stored results (grounded).
6. Real P04 tools verify the fixed page → PASS.
7. The AI explains the success.

Prerequisites: ANTHROPIC_API_KEY in .env, the fixture server on 8020 and the API on 8010
(see docs/VERIFICATION.md). Human review decisions in step 3 are scripted here and printed
as such; in the product they are made in the UI.

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
        "kind": "p05-ai-demo",
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
    for c in draft.review["criteria"]:
        print(
            f"  [{c['kind']}] {c['key']} blocking={c['blocking']} expected={c['expected']}"
            f" needs={c['needs_configuration']}\n      quote: {c['source_quote']!r}"
        )
    print("  rejected:", draft.review["rejected_criteria"])
    print("  ambiguities:", draft.review["ambiguities"])
    print("  manual review:", draft.review["manual_review_items"])
    t["steps"]["2_ai_draft"] = {
        "ai_call": meta,
        "ai_output": draft.ai_output,
        "deterministic_review": draft.review,
    }

    banner(3, "Buyer review → accept → propose → approve (human decisions, scripted)")
    keys = {c["key"]: c for c in draft.review["criteria"]}
    manual = [k for k, c in keys.items() if c["kind"] == "manual_review"]
    decisions = {
        "use_standard_form_fields": True,  # buyer opts into the standard test form config
        "blocking_overrides": dict.fromkeys(manual, False),  # checked manually, separately
        "configuration": {},
    }
    http_keys = [k for k, c in keys.items() if c["kind"] == "http_availability"]
    for k in http_keys:  # local fixtures are http://, buyer relaxes HTTPS for the demo
        decisions["configuration"][k] = {"require_https": False}
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
    svc = AgreementService(session=session)
    v = agreement.versions[0]
    p = svc.propose(alice, agreement.id, 1, expected_revision=v.revision)
    svc.approve(bob, agreement.id, 1, content_hash_seen=p.content_hash)
    session.expire_all()
    agreement = svc.get(alice, agreement.id)
    print(
        f"agreement {agreement.id} status={agreement.status.value} "
        f"locked_version={agreement.current_locked_version_id}"
    )
    t["steps"]["3_human_review"] = {
        "decisions": decisions,
        "agreement_id": agreement.id,
        "content_hash": p.content_hash,
        "approved_by": "bob@acme.demo (buyer_approver)",
    }

    vs = VerifierSettings()
    worker = new_worker_id()
    evidence = EvidenceIntelligence(session, gw)
    for step, variant in ((4, "landing-page-broken"), (6, "landing-page-fixed")):
        banner(step, f"Real verification: {variant}")
        run = submit_evidence(session, maya, agreement, delivery_url=f"{FIXTURES}/{variant}/")
        job = claim_job(session, worker, vs.lease_seconds)
        t0 = time.monotonic()
        Orchestrator(session, vs, worker).process(job)
        session.expire_all()
        run = session.get(VerificationRun, run.id)
        results = [
            (r.criterion_key, r.blocking, r.outcome.value, r.reason_code) for r in run.results
        ]
        print(f"verdict={run.verdict.value} ({time.monotonic() - t0:.1f}s)")
        for r in results:
            print("  ", r)

        banner(step + 1, f"AI explanation of {variant}")
        a = evidence.analyze(run.id, actor="user:bob@acme.demo")
        meta = call_meta(session, a.ai_call_id)
        print(json.dumps(meta, indent=1))
        print(f"status={a.status.value}  verdict(deterministic)={a.run_verdict}")
        print("summary:", a.grounded["summary"])
        for e in a.grounded["explanations"]:
            print(
                f"  - {e['criterion_key']} [{e['outcome']}/{e['reason_code']}] "
                f"confidence={e['confidence']}\n    {e['explanation']}\n"
                f"    refs={e['evidence_refs']}\n    remediation={e['remediation']}"
            )
        print("  rejected claims:", a.grounded.get("rejected_claims"))
        print("  unexplained:", a.grounded.get("unexplained_criteria"))
        t["steps"][f"{step}_verification_{variant}"] = {
            "run_id": run.id,
            "verdict": run.verdict.value,
            "results": results,
        }
        t["steps"][f"{step + 1}_ai_explanation_{variant}"] = {
            "ai_call": meta,
            "status": a.status.value,
            "grounded": a.grounded,
            "ai_output": a.ai_output,
        }

    out = (
        REPO
        / "docs"
        / "integration"
        / "runs"
        / (f"ai-demo-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json")
    )
    out.write_text(json.dumps(t, indent=2, default=str))
    print(f"\ntranscript: {out.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
