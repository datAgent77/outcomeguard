"""P04 demonstration: verify the real broken and fixed landing pages with the real worker.

Prerequisites (local development):
    python fixtures/serve.py --port 8020 --sink-url http://127.0.0.1:8010/sinks/default
    PUBLIC_API_URL=http://localhost:8010 uv run uvicorn outcomeguard.main:app --port 8010
    .env: SINK_SECRET, SINK_BASE_URL=http://127.0.0.1:8010,
          VERIFIER_DEV_ALLOW=127.0.0.1:8020,127.0.0.1:8010

    uv run python scripts/verification_demo.py

Creates (or reuses) demo identities, creates and locks a landing-page agreement, submits
both fixtures as evidence, runs the verification worker on each, and writes a transcript
of the actual results and artifact hashes to docs/integration/runs/.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from outcomeguard.auth import load_principal
from outcomeguard.domain.criteria import landing_page_template
from outcomeguard.models import EvidenceArtifact, PaymentEnvironment, VerificationRun
from outcomeguard.money import Money
from outcomeguard.services.agreements import AgreementService, DraftContent
from outcomeguard.services.identities import ensure_demo_identities
from outcomeguard.verification.service import (
    Orchestrator,
    claim_job,
    is_verified_pass,
    new_worker_id,
    submit_evidence,
)
from outcomeguard.verification.settings import assert_no_paypal_credentials, get_verifier_settings

REPO = Path(__file__).resolve().parents[3]
FIXTURES = "http://127.0.0.1:8020"


def main() -> int:
    assert_no_paypal_credentials()
    settings = get_verifier_settings()
    session = sessionmaker(bind=create_engine(settings.database_url), expire_on_commit=False)()
    ids = ensure_demo_identities(session, PaymentEnvironment.SANDBOX)
    alice = load_principal(session, ids.users["alice@acme.demo"])
    bob = load_principal(session, ids.users["bob@acme.demo"])
    maya = load_principal(session, ids.users["maya@northwind.demo"])

    criteria = landing_page_template()
    criteria[0] = criteria[0].model_copy(
        update={"spec": criteria[0].spec.model_copy(update={"require_https": False})}
    )
    svc = AgreementService(session=session)
    agreement = svc.create(
        alice,
        buyer_organization_id=ids.buyer_org_id,
        merchant_account_id=ids.merchant.id,
        content=DraftContent(
            title="Spring campaign landing page (P04 demo)",
            description="Local fixture demo",
            requirements_text=(
                "Build a responsive landing page for $500. The contact form must work, mobile "
                "layout must pass verification, and Lighthouse performance score must be at "
                "least 90."
            ),
            amount=Money(50000, "USD"),
            delivery_deadline=datetime.now(UTC) + timedelta(days=14),
        ),
        criteria=criteria,
    )
    v = agreement.versions[0]
    p = svc.propose(alice, agreement.id, 1, expected_revision=v.revision)
    svc.approve(bob, agreement.id, 1, content_hash_seen=p.content_hash)
    session.expire_all()
    agreement = svc.get(alice, agreement.id)

    worker_id = new_worker_id()
    transcript: dict = {
        "kind": "p04-verification-demo",
        "started_at": datetime.now(UTC).isoformat(),
        "agreement_id": agreement.id,
        "agreement_version_id": agreement.current_locked_version_id,
        "agreement_content_hash": p.content_hash,
        "runs": [],
    }
    for variant in ("landing-page-broken", "landing-page-fixed"):
        url = f"{FIXTURES}/{variant}/"
        run = submit_evidence(session, maya, agreement, delivery_url=url)
        job = claim_job(session, worker_id, settings.lease_seconds)
        assert job is not None and job.run_id == run.id
        t0 = time.monotonic()
        Orchestrator(session, settings, worker_id).process(job)
        elapsed = time.monotonic() - t0
        session.expire_all()
        run = session.get(VerificationRun, run.id)
        arts = session.scalars(
            select(EvidenceArtifact).where(EvidenceArtifact.run_id == run.id)
        ).all()
        entry = {
            "fixture": variant,
            "delivery_url": url,
            "run_id": run.id,
            "status": run.status.value,
            "verdict": run.verdict.value if run.verdict else None,
            "verified_pass": is_verified_pass(session, run.id),
            "wall_seconds": round(elapsed, 1),
            "tool_versions": run.tool_versions,
            "results": [
                {
                    "criterion": r.criterion_key,
                    "blocking": r.blocking,
                    "outcome": r.outcome.value,
                    "reason": r.reason_code,
                    "duration_ms": r.duration_ms,
                    "measured": {
                        k: r.measured.get(k)
                        for k in (
                            "chain",
                            "scroll_width",
                            "viewport_width",
                            "success_text_seen",
                            "sink_requests",
                            "prohibited_requests",
                            "sink_receipts_in_db",
                            "scores",
                            "adaptive_retry",
                            "error_code",
                        )
                        if k in r.measured
                    },
                    "lighthouse_run_ms": [x.get("elapsed_ms") for x in r.measured.get("runs", [])]
                    or None,
                }
                for r in run.results
            ],
            "artifacts": [
                {
                    "label": a.label,
                    "kind": a.kind.value,
                    "sha256": a.sha256,
                    "size_bytes": a.size_bytes,
                }
                for a in arts
            ],
        }
        transcript["runs"].append(entry)
        print(f"{variant}: {entry['status']} / {entry['verdict']} ({elapsed:.1f}s)")
        for r in entry["results"]:
            print(f"   {r['criterion']:<24} {r['outcome']:<6} {r['reason']}")

    out = (
        REPO
        / "docs"
        / "integration"
        / "runs"
        / (f"verification-demo-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json")
    )
    out.write_text(json.dumps(transcript, indent=2, default=str))
    print(f"transcript: {out.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
