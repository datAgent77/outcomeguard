"""P06 end-to-end governance demo against the REAL PayPal Sandbox and REAL verification.

Real: PayPal Sandbox order/authorize/capture/reads, Chromium + Lighthouse verification of
the broken/fixed fixtures, PostgreSQL. Scripted: the human actors' decisions (printed as
such). Simulated (and labeled): the *loss* of one capture response — the PayPal capture
call itself is real; only its response is discarded client-side to exercise UNKNOWN →
reconcile, which replays the same PayPal-Request-Id against the real Sandbox.

Prerequisites: PAYMENT_MODE=sandbox + PayPal Sandbox credentials in .env, fixtures on
8020, API on 8010 (sink), a human to approve the PayPal payment in the browser.

    uv run python scripts/governance_demo.py [--amount 500.00]
"""

from __future__ import annotations

import argparse
import json
import time
import webbrowser
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from outcomeguard.auth import Forbidden, load_principal
from outcomeguard.config import PaymentMode, Settings
from outcomeguard.domain.criteria import CriterionInput, landing_page_template
from outcomeguard.models import (
    OrganizationMembership,
    PaymentCapture,
    PaymentEnvironment,
    PolicyDecisionRecord,
    ReviewDecision,
    Role,
    User,
    UserKind,
    VerificationRun,
)
from outcomeguard.money import Money
from outcomeguard.paypal import get_gateway
from outcomeguard.paypal.gateway import PayPalUncertainError
from outcomeguard.services.agreements import AgreementService, DraftContent
from outcomeguard.services.governance import Governance, GovernanceError, evidence_snapshot_sha256
from outcomeguard.services.identities import ensure_demo_identities
from outcomeguard.services.payments import PaymentRuleViolation, PaymentService, PaymentUncertain
from outcomeguard.verification.service import (
    Orchestrator,
    claim_job,
    new_worker_id,
    submit_evidence,
)
from outcomeguard.verification.settings import VerifierSettings

REPO = Path(__file__).resolve().parents[3]
FIXTURES = "http://127.0.0.1:8020"
LOG: list[dict[str, Any]] = []


class DropFirstCaptureResponse:
    """Wraps the real gateway. The first capture call is really sent to PayPal; its
    response is then discarded (SIMULATED network loss)."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.simulated = inner.simulated
        self.dropped = False

    def capture_authorization(self, authorization_id, body, *, request_id):
        resp = self.inner.capture_authorization(authorization_id, body, request_id=request_id)
        if not self.dropped:
            self.dropped = True
            LOG.append(
                {
                    "event": "SIMULATED_RESPONSE_LOSS",
                    "request_id": request_id,
                    "note": "real PayPal capture was sent; its response was discarded",
                }
            )
            raise PayPalUncertainError("simulated lost response after a real PayPal capture")
        return resp

    def __getattr__(self, name):
        return getattr(self.inner, name)


def step(title: str, **facts: Any) -> None:
    print(f"\n== {title}")
    for k, v in facts.items():
        print(f"   {k}: {v}")
    LOG.append(
        {
            "step": title,
            **{
                k: v if isinstance(v, (str, int, float, bool, list, dict, type(None))) else str(v)
                for k, v in facts.items()
            },
        }
    )


def attempt(label: str, fn) -> Any:
    try:
        out = fn()
        step(f"{label}: ALLOWED")
        return out
    except GovernanceError as e:
        step(f"{label}: BLOCKED", code=e.code, reasons=[r["code"] for r in e.reasons])
    except PaymentRuleViolation as e:
        step(f"{label}: BLOCKED", code=e.code)
    except Forbidden as e:
        step(f"{label}: REJECTED", reason=str(e))
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--amount", default="500.00")
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()
    settings = Settings()
    if settings.payment_mode is not PaymentMode.SANDBOX or not settings.paypal_configured:
        print("Needs PAYMENT_MODE=sandbox and PayPal Sandbox credentials.")
        return 2
    s = sessionmaker(bind=create_engine(settings.database_url), expire_on_commit=False)()
    gateway = DropFirstCaptureResponse(get_gateway())
    payments = PaymentService(session=s, gateway=gateway, settings=settings)
    gov = Governance(session=s, payments=payments)
    ids = ensure_demo_identities(s, PaymentEnvironment.SANDBOX)

    def who(name: str):
        email = next(e for e in ids.users if e.startswith(name))
        return load_principal(s, ids.users[email])

    started = datetime.now(UTC)

    # 1. Locked agreement with a BLOCKING manual accessibility criterion.
    crits = landing_page_template()
    crits[0] = crits[0].model_copy(
        update={"spec": crits[0].spec.model_copy(update={"require_https": False})}
    )
    crits.append(
        CriterionInput(
            key="website_accessible",
            title="Website is accessible",
            spec={"kind": "manual_review", "instructions": "Check against WCAG 2.2 AA"},
        )
    )
    svc = AgreementService(session=s)
    agreement = svc.create(
        who("alice"),
        buyer_organization_id=ids.buyer_org_id,
        merchant_account_id=ids.merchant.id,
        content=DraftContent(
            title="Spring campaign landing page (P06 governance demo)",
            description="",
            requirements_text="Landing page, $500",
            amount=Money.from_decimal_str(args.amount, "USD"),
            delivery_deadline=datetime.now(UTC) + timedelta(days=14),
        ),
        criteria=crits,
    )
    p = svc.propose(who("alice"), agreement.id, 1, expected_revision=agreement.versions[0].revision)
    svc.approve(who("bob"), agreement.id, 1, content_hash_seen=p.content_hash)
    s.expire_all()
    agreement = svc.get(who("alice"), agreement.id)
    step(
        "Agreement locked",
        agreement_id=agreement.id,
        content_hash=p.content_hash,
        criteria=[c.key for c in agreement.versions[0].criteria],
    )

    # 2. Real PayPal Sandbox order + human approval + authorization.
    from outcomeguard.models import AgreementVersion

    version = s.get(AgreementVersion, agreement.current_locked_version_id)
    order = payments.create_order_for_agreement(version, actor="user:alice@acme.demo")
    step(
        "PayPal Sandbox order created (intent AUTHORIZE)",
        paypal_order_id=order.paypal_order_id,
        amount=f"{args.amount} USD",
        approval_url=order.approval_url,
    )
    print(f"\n   >>> Approve as the Sandbox PERSONAL buyer: {order.approval_url}")
    webbrowser.open(order.approval_url or "")
    deadline = time.monotonic() + args.timeout
    while True:
        try:
            if payments.sync_order(order).paypal_status == "APPROVED":
                break
        except PayPalUncertainError:
            payments.session.rollback()  # transient Sandbox 5xx/timeout while polling: retry
        if time.monotonic() > deadline:
            print("timed out waiting for buyer approval")
            return 1
        time.sleep(3)
    auth = payments.authorize_order(order.id, actor="user:bob@acme.demo")
    step(
        "Authorized (real Sandbox)",
        paypal_authorization_id=auth.paypal_authorization_id,
        status=auth.paypal_status,
        expires_at=str(auth.expires_at),
    )

    vs = VerifierSettings()
    worker = new_worker_id()

    def verify(variant: str) -> VerificationRun:
        run = submit_evidence(s, who("maya"), agreement, delivery_url=f"{FIXTURES}/{variant}/")
        job = claim_job(s, worker, vs.lease_seconds)
        Orchestrator(s, vs, worker).process(job)
        s.expire_all()
        run = s.get(VerificationRun, run.id)
        step(
            f"Real verification: {variant}",
            run_id=run.id,
            verdict=run.verdict.value,
            results={r.criterion_key: r.outcome.value for r in run.results},
        )
        return run

    def policy(label: str):
        row = gov.preview(who("bob"), auth.id)
        step(
            f"Policy ({label})",
            decision=row.decision.value,
            reasons=[r["code"] for r in row.reasons],
            inputs_sha256=row.inputs_sha256,
        )
        return row

    def review(run: VerificationRun, name: str = "rita"):
        crit = next(r.criterion_id for r in run.results if r.criterion_key == "website_accessible")
        return gov.record_review(
            who(name),
            run_id=run.id,
            criterion_id=crit,
            decision=ReviewDecision.SATISFIED,
            rationale="Manual WCAG 2.2 AA check: keyboard navigation, contrast, form labels OK.",
            evidence_snapshot_sha256_seen=evidence_snapshot_sha256(s, run),
        )

    # 3. Broken → FAIL → blocked.
    verify("landing-page-broken")
    pol = policy("after broken page")
    attempt(
        "Capture approval on FAIL",
        lambda: gov.approve_capture(who("bob"), auth.id, pol.inputs_sha256),
    )
    # 4. Fixed → NEEDS_MANUAL_REVIEW → blocked.
    fixed = verify("landing-page-fixed")
    pol = policy("after fixed page")
    attempt(
        "Capture approval while accessibility is unreviewed",
        lambda: gov.approve_capture(who("bob"), auth.id, pol.inputs_sha256),
    )
    # 5. Provider self-review and an AI service account are rejected.
    attempt("Provider (maya) reviews own delivery", lambda: review(fixed, "maya"))
    bot = s.scalar(select(User).where(User.email == "ai-assistant@system.local"))
    if bot is None:
        bot = User(
            email="ai-assistant@system.local", display_name="AI assistant", kind=UserKind.SERVICE
        )
        s.add(bot)
        s.flush()
        s.add(
            OrganizationMembership(
                organization_id=ids.buyer_org_id, user_id=bot.id, role=Role.BUYER_REVIEWER
            )
        )
        s.commit()
    attempt(
        "AI service account submits a review",
        lambda: gov.record_review(
            load_principal(s, bot.id),
            run_id=fixed.id,
            criterion_id=next(
                r.criterion_id for r in fixed.results if r.criterion_key == "website_accessible"
            ),
            decision=ReviewDecision.SATISFIED,
            rationale="AI considers it accessible.",
            evidence_snapshot_sha256_seen=evidence_snapshot_sha256(s, fixed),
        ),
    )
    # 6. Authorized human review → eligible → buyer approval.
    d = review(fixed)
    step(
        "Human manual review recorded",
        reviewer="rita@acme.demo (buyer_reviewer)",
        decision=d.decision.value,
        evidence_snapshot_sha256=d.evidence_snapshot_sha256,
    )
    pol = policy("after human review")
    approval = gov.approve_capture(who("bob"), auth.id, pol.inputs_sha256)
    step("Buyer capture approval", approval_id=approval.id, inputs_sha256=approval.inputs_sha256)
    # 7. Stale evidence: new submission after approval → blocked; re-verify, re-review, re-approve.
    submit_evidence(s, who("maya"), agreement, delivery_url=f"{FIXTURES}/landing-page-fixed/")
    attempt(
        "Capture with approval after NEW evidence was submitted",
        lambda: gov.capture(who("bob"), auth.id, approval.id),
    )
    job = claim_job(s, worker, vs.lease_seconds)
    Orchestrator(s, vs, worker).process(job)
    s.expire_all()
    fresh = gov.latest_completed_run(version.id)
    step("Re-verified latest evidence", run_id=fresh.id, verdict=fresh.verdict.value)
    policy("new run, old review no longer applies")
    review(fresh)
    pol = policy("after review of the new run")
    attempt("Capture with the OLD approval", lambda: gov.capture(who("bob"), auth.id, approval.id))
    approval = gov.approve_capture(who("bob"), auth.id, pol.inputs_sha256)
    step("Fresh buyer capture approval", approval_id=approval.id)
    # 8. Real capture with a simulated lost response → UNKNOWN.
    try:
        gov.capture(who("bob"), auth.id, approval.id)
    except PaymentUncertain as e:
        cap = s.get(PaymentCapture, e.entity_id)
        step(
            "Capture outcome UNKNOWN (response lost)",
            capture_id=cap.id,
            status=cap.status.value,
            request_id=cap.request_id,
        )
    attempt("Second capture while UNKNOWN", lambda: gov.capture(who("bob"), auth.id, approval.id))
    # 9. Reconcile → same PayPal-Request-Id replayed against the real Sandbox.
    cap = gov.reconcile(who("bob"), cap.id)
    remote = gateway.inner.get_capture(cap.paypal_capture_id)
    step(
        "Reconciled (real Sandbox replay of the same PayPal-Request-Id)",
        paypal_capture_id=cap.paypal_capture_id,
        status=cap.status.value,
        paypal_status=remote["status"],
        amount=remote["amount"],
        request_id=cap.request_id,
    )
    # 10. Duplicate capture attempts are blocked.
    attempt(
        "Duplicate capture with the used approval",
        lambda: gov.capture(who("bob"), auth.id, approval.id),
    )
    pol = policy("after capture")
    attempt(
        "New approval after capture",
        lambda: gov.approve_capture(who("bob"), auth.id, pol.inputs_sha256),
    )
    chain = [
        (r.purpose, r.decision.value, [x["code"] for x in r.reasons])
        for r in s.scalars(
            select(PolicyDecisionRecord)
            .where(PolicyDecisionRecord.payment_authorization_id == auth.id)
            .order_by(PolicyDecisionRecord.evaluated_at)
        )
    ]
    step("Policy decision trail", decisions=chain)

    out = (
        REPO
        / "docs"
        / "integration"
        / "runs"
        / (f"governance-demo-{started.strftime('%Y%m%dT%H%M%SZ')}.json")
    )
    out.write_text(
        json.dumps(
            {
                "kind": "p06-governance-demo",
                "environment": "paypal-sandbox",
                "started_at": started.isoformat(),
                "finished_at": datetime.now(UTC).isoformat(),
                "simulated": ["one capture response discarded client-side"],
                "log": LOG,
            },
            indent=2,
            default=str,
        )
    )
    print(f"\ntranscript: {out.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
