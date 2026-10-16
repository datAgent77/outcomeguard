"""P06 end-to-end governance demo against the REAL PayPal Sandbox and REAL verification.

Actors are labeled in every step:
  HUMAN     — the person approving the payment in the PayPal Sandbox browser (only step
              performed by a human);
  SCRIPTED  — this script acting through a demo account (alice/bob/rita/maya or the
              service account). These are NOT human decisions; the manual-review rationale
              says so explicitly in the database;
  SYSTEM    — OutcomeGuard itself (verification worker, policy engine, reconciliation).

Real: PayPal Sandbox order/authorize/capture and reads, Chromium + Lighthouse verification
of the broken/fixed fixtures, PostgreSQL. Simulated (labeled): the LOSS of the capture
response — the single capture POST is really sent; its response is discarded client-side.
Reconciliation then reads PayPal's state; no additional capture is issued.

The approval URL is printed once to stdout for the human; it is not stored in the
transcript. Prerequisites: PAYMENT_MODE=sandbox + Sandbox credentials, fixtures on 8020,
API on 8010.

    uv run python scripts/governance_demo.py [--amount 500.00] [--approval-timeout 1800]
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
    AgreementVersion,
    CaptureApproval,
    OrganizationMembership,
    PaymentAuthorization,
    PaymentCapture,
    PaymentEnvironment,
    PaymentOrder,
    PolicyDecisionRecord,
    ReviewDecision,
    Role,
    User,
    UserKind,
    VerificationRun,
)
from outcomeguard.money import Money
from outcomeguard.paypal import get_gateway
from outcomeguard.paypal.gateway import PayPalAPIError, PayPalUncertainError
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
HUMAN = "HUMAN (buyer approving in the PayPal Sandbox browser)"
SYSTEM = "SYSTEM (OutcomeGuard)"
SCRIPTED_RATIONALE = (
    "SCRIPTED DEMO DECISION: recorded by the governance demo script through "
    "the buyer reviewer demo account. No human performed an accessibility "
    "review."
)
LOG: list[dict[str, Any]] = []
T0 = time.monotonic()


def scripted(email: str) -> str:
    return f"SCRIPTED demo account {email} (not a human decision)"


class DropFirstCaptureResponse:
    """Wraps the real gateway. The first capture POST is really sent to PayPal; its response
    is then discarded (SIMULATED network loss). Every other call passes through."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.simulated = inner.simulated
        self.capture_posts = 0
        self.dropped = False

    def capture_authorization(self, authorization_id, body, *, request_id):
        self.capture_posts += 1
        resp = self.inner.capture_authorization(authorization_id, body, request_id=request_id)
        if not self.dropped:
            self.dropped = True
            raise PayPalUncertainError("SIMULATED: response discarded after a real capture POST")
        return resp

    def __getattr__(self, name):
        return getattr(self.inner, name)


def step(title: str, actor: str, **facts: Any) -> None:
    t = round(time.monotonic() - T0, 1)
    print(f"\n[t+{t:>6}s] {title}\n   actor: {actor}")
    for k, v in facts.items():
        print(f"   {k}: {v}")
    LOG.append(
        {
            "t_seconds": t,
            "step": title,
            "actor": actor,
            **{
                k: v if isinstance(v, (str, int, float, bool, list, dict, type(None))) else str(v)
                for k, v in facts.items()
            },
        }
    )


def attempt(label: str, actor: str, fn) -> Any:
    try:
        out = fn()
        step(f"{label}: ALLOWED", actor)
        return out
    except GovernanceError as e:
        step(f"{label}: BLOCKED", actor, code=e.code, reasons=[r["code"] for r in e.reasons])
    except PaymentRuleViolation as e:
        step(f"{label}: BLOCKED", actor, code=e.code)
    except Forbidden as e:
        step(f"{label}: REJECTED", actor, reason=str(e))
    return None


def sanitize_capture(c: dict) -> dict:
    keep = (
        "id",
        "status",
        "amount",
        "final_capture",
        "invoice_id",
        "create_time",
        "update_time",
        "seller_receivable_breakdown",
    )
    return {k: c.get(k) for k in keep if k in c}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--amount", default="500.00")
    ap.add_argument("--approval-timeout", type=int, default=1800, help="seconds")
    ap.add_argument("--poll-interval", type=float, default=5.0)
    ap.add_argument(
        "--max-transient-failures",
        type=int,
        default=12,
        help="consecutive PayPal 5xx/timeouts tolerated while polling",
    )
    args = ap.parse_args()
    settings = Settings()
    if settings.payment_mode is not PaymentMode.SANDBOX or not settings.paypal_configured:
        print("Needs PAYMENT_MODE=sandbox and PayPal Sandbox credentials.")
        return 2
    s = sessionmaker(bind=create_engine(settings.database_url), expire_on_commit=False)()
    real = get_gateway()
    gateway = DropFirstCaptureResponse(real)
    payments = PaymentService(session=s, gateway=gateway, settings=settings)
    gov = Governance(session=s, payments=payments)
    ids = ensure_demo_identities(s, PaymentEnvironment.SANDBOX)
    started = datetime.now(UTC)
    out = (
        REPO
        / "docs"
        / "integration"
        / "runs"
        / (f"governance-demo-{started.strftime('%Y%m%dT%H%M%SZ')}.json")
    )
    status = "incomplete"

    def who(name: str):
        email = next(e for e in ids.users if e.startswith(name))
        return load_principal(s, ids.users[email])

    try:
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
                requirements_text="Landing page, accessible",
                amount=Money.from_decimal_str(args.amount, "USD"),
                delivery_deadline=datetime.now(UTC) + timedelta(days=14),
            ),
            criteria=crits,
        )
        p = svc.propose(
            who("alice"), agreement.id, 1, expected_revision=agreement.versions[0].revision
        )
        svc.approve(who("bob"), agreement.id, 1, content_hash_seen=p.content_hash)
        s.expire_all()
        agreement = svc.get(who("alice"), agreement.id)
        version = s.get(AgreementVersion, agreement.current_locked_version_id)
        step(
            "Agreement created, proposed and locked",
            scripted("alice/bob@acme.demo"),
            agreement_id=agreement.id,
            version_id=version.id,
            content_hash=p.content_hash,
            criteria={
                c.key: ("blocking" if c.blocking else "non-blocking") for c in version.criteria
            },
        )

        # 2. One fresh PayPal Sandbox order; HUMAN approval; authorization.
        order = payments.create_order_for_agreement(version, actor="scripted:alice@acme.demo")
        step(
            "PayPal Sandbox order created (intent AUTHORIZE)",
            SYSTEM,
            paypal_order_id=order.paypal_order_id,
            amount=f"{args.amount} USD",
            paypal_status=order.paypal_status,
        )
        print(
            f"\n   >>> HUMAN ACTION — approve as the Sandbox PERSONAL buyer:\n"
            f"   >>> {order.approval_url}\n"
        )
        webbrowser.open(order.approval_url or "")
        deadline = time.monotonic() + args.approval_timeout
        transient, total_transient = 0, 0
        while True:
            try:
                if payments.sync_order(order).paypal_status == "APPROVED":
                    break
                transient = 0
            except PayPalUncertainError:
                s.rollback()
                transient += 1
                total_transient += 1
                if transient > args.max_transient_failures:
                    step(
                        "Stopped: PayPal kept failing while polling",
                        SYSTEM,
                        consecutive_transient_failures=transient,
                    )
                    return 1
            if time.monotonic() > deadline:
                step(
                    "Stopped: buyer approval not received in time",
                    SYSTEM,
                    approval_timeout_seconds=args.approval_timeout,
                )
                return 1
            time.sleep(args.poll_interval)
        step(
            "Buyer approved the payment at PayPal (read server-side via GET order)",
            HUMAN,
            paypal_status="APPROVED",
            payer_recorded=bool(order.payer_id),
            transient_poll_failures_retried=total_transient,
        )
        auth = payments.authorize_order(order.id, actor="scripted:bob@acme.demo")
        step(
            "Authorized (real Sandbox)",
            SYSTEM,
            paypal_authorization_id=auth.paypal_authorization_id,
            paypal_status=auth.paypal_status,
            amount=f"{args.amount} USD",
            expires_at=str(auth.expires_at),
            db_status=auth.status.value,
        )

        vs = VerifierSettings()
        worker = new_worker_id()

        def run_worker() -> VerificationRun:
            job = claim_job(s, worker, vs.lease_seconds)
            Orchestrator(s, vs, worker).process(job)
            s.expire_all()
            return s.get(VerificationRun, job.run_id)

        def verify(variant: str) -> VerificationRun:
            submit_evidence(s, who("maya"), agreement, delivery_url=f"{FIXTURES}/{variant}/")
            run = run_worker()
            step(
                f"Evidence submitted ({variant}) and verified with Chromium + Lighthouse",
                f"{scripted('maya@northwind.demo')} submits; {SYSTEM} verifies",
                run_id=run.id,
                verdict=run.verdict.value,
                results={r.criterion_key: r.outcome.value for r in run.results},
                evidence_snapshot_sha256=evidence_snapshot_sha256(s, run),
            )
            return run

        def policy(label: str):
            row = gov.preview(who("bob"), auth.id)
            step(
                f"Policy evaluation — {label}",
                SYSTEM,
                decision=row.decision.value,
                reasons=[r["code"] for r in row.reasons],
                inputs_sha256=row.inputs_sha256,
                policy_decision_id=row.id,
            )
            return row

        def manual_crit(run: VerificationRun) -> str:
            return next(
                r.criterion_id for r in run.results if r.criterion_key == "website_accessible"
            )

        def review(run: VerificationRun, name: str = "rita"):
            return gov.record_review(
                who(name),
                run_id=run.id,
                criterion_id=manual_crit(run),
                decision=ReviewDecision.SATISFIED,
                rationale=SCRIPTED_RATIONALE,
                evidence_snapshot_sha256_seen=evidence_snapshot_sha256(s, run),
            )

        # 3. Broken → FAIL → blocked.
        verify("landing-page-broken")
        pol = policy("after broken page")
        attempt(
            "Capture approval while verification FAILED",
            scripted("bob@acme.demo"),
            lambda: gov.approve_capture(who("bob"), auth.id, pol.inputs_sha256),
        )
        # 4. Fixed → NEEDS_MANUAL_REVIEW → blocked.
        fixed = verify("landing-page-fixed")
        pol = policy("after fixed page")
        attempt(
            "Capture approval while accessibility is unreviewed",
            scripted("bob@acme.demo"),
            lambda: gov.approve_capture(who("bob"), auth.id, pol.inputs_sha256),
        )
        # 5. Provider and service-account reviews are rejected.
        attempt(
            "Manual review by the provider (own delivery)",
            scripted("maya@northwind.demo"),
            lambda: review(fixed, "maya"),
        )
        bot = s.scalar(select(User).where(User.email == "ai-assistant@system.local"))
        if bot is None:
            bot = User(
                email="ai-assistant@system.local",
                display_name="AI assistant",
                kind=UserKind.SERVICE,
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
            "Manual review by a service account (AI integration)",
            "SCRIPTED service account ai-assistant@system.local",
            lambda: gov.record_review(
                load_principal(s, bot.id),
                run_id=fixed.id,
                criterion_id=manual_crit(fixed),
                decision=ReviewDecision.SATISFIED,
                rationale="AI considers it accessible.",
                evidence_snapshot_sha256_seen=evidence_snapshot_sha256(s, fixed),
            ),
        )
        # 6. Manual review (scripted reviewer account) → eligible → buyer capture approval.
        d = review(fixed)
        step(
            "Manual review decision recorded",
            scripted("rita@acme.demo (buyer_reviewer)"),
            decision_id=d.id,
            decision=d.decision.value,
            run_id=d.verification_run_id,
            evidence_snapshot_sha256=d.evidence_snapshot_sha256,
            rationale=d.rationale,
        )
        pol = policy("after manual review")
        approval = gov.approve_capture(who("bob"), auth.id, pol.inputs_sha256)
        step(
            "Buyer capture approval recorded",
            scripted("bob@acme.demo (buyer_approver)"),
            approval_id=approval.id,
            bound_inputs_sha256=approval.inputs_sha256,
        )
        # 7. Stale evidence invalidates the approval.
        submit_evidence(s, who("maya"), agreement, delivery_url=f"{FIXTURES}/landing-page-fixed/")
        step("New evidence submitted after the approval", scripted("maya@northwind.demo"))
        attempt(
            "Capture with the pre-existing approval",
            scripted("bob@acme.demo"),
            lambda: gov.capture(who("bob"), auth.id, approval.id),
        )
        # 8. Fresh verification + review + approval restore eligibility.
        fresh = run_worker()
        step(
            "Latest evidence verified",
            SYSTEM,
            run_id=fresh.id,
            verdict=fresh.verdict.value,
            evidence_snapshot_sha256=evidence_snapshot_sha256(s, fresh),
        )
        policy("new run — the earlier review is bound to the old run")
        d2 = review(fresh)
        step(
            "Manual review decision recorded for the new run",
            scripted("rita@acme.demo (buyer_reviewer)"),
            decision_id=d2.id,
            evidence_snapshot_sha256=d2.evidence_snapshot_sha256,
        )
        pol = policy("after review of the new run")
        attempt(
            "Capture with the OLD approval",
            scripted("bob@acme.demo"),
            lambda: gov.capture(who("bob"), auth.id, approval.id),
        )
        approval = gov.approve_capture(who("bob"), auth.id, pol.inputs_sha256)
        step(
            "Fresh buyer capture approval recorded",
            scripted("bob@acme.demo (buyer_approver)"),
            approval_id=approval.id,
            bound_inputs_sha256=approval.inputs_sha256,
        )
        # 9. Real capture; the response is lost → UNKNOWN.
        cap_id = None
        try:
            gov.capture(who("bob"), auth.id, approval.id)
        except PaymentUncertain as e:
            cap_id = e.entity_id
        s.expire_all()
        cap = s.get(PaymentCapture, cap_id)
        a_row = s.get(PaymentAuthorization, auth.id)
        step(
            "Capture POST sent to PayPal; response LOST (simulated) → UNKNOWN",
            f"{scripted('bob@acme.demo')} requests; {SYSTEM} reserves and calls PayPal",
            capture_id=cap.id,
            db_capture_status=cap.status.value,
            db_authorization_status=a_row.status.value,
            request_id=cap.request_id,
            capture_posts_sent=gateway.capture_posts,
            policy_decision_id=cap.policy_decision_id,
            approval_id=cap.approval_id,
            verification_run_id=cap.verification_run_id,
            approval_consumed_by=s.get(CaptureApproval, approval.id).consumed_by_capture_id,
        )
        attempt(
            "Second capture attempt while UNKNOWN",
            scripted("bob@acme.demo"),
            lambda: gov.capture(who("bob"), auth.id, approval.id),
        )
        # 10. Reconcile: read PayPal's actual state; no new capture POST.
        cap = gov.reconcile(who("bob"), cap.id)
        remote = real.get_capture(cap.paypal_capture_id)
        order_state = real.get_order(order.paypal_order_id)
        a_remote = real.get_authorization(auth.paypal_authorization_id)
        s.expire_all()
        step(
            "Reconciled from PayPal's actual state",
            SYSTEM,
            method=payments.last_reconcile_method,
            capture_posts_sent=gateway.capture_posts,
            db_capture_status=cap.status.value,
            db_authorization_status=s.get(PaymentAuthorization, auth.id).status.value,
            paypal_capture=sanitize_capture(remote),
            paypal_authorization_status=a_remote["status"],
            paypal_order_status=order_state["status"],
            paypal_order_capture_count=len(
                order_state["purchase_units"][0].get("payments", {}).get("captures", [])
            ),
        )
        # 11. Duplicates are blocked.
        attempt(
            "Duplicate capture with the used approval",
            scripted("bob@acme.demo"),
            lambda: gov.capture(who("bob"), auth.id, approval.id),
        )
        pol = policy("after capture")
        attempt(
            "New capture approval after capture",
            scripted("bob@acme.demo"),
            lambda: gov.approve_capture(who("bob"), auth.id, pol.inputs_sha256),
        )
        trail = [
            {
                "purpose": r.purpose,
                "decision": r.decision.value,
                "reasons": [x["code"] for x in r.reasons],
                "inputs_sha256": r.inputs_sha256,
                "evaluated_at": r.evaluated_at.isoformat(),
            }
            for r in s.scalars(
                select(PolicyDecisionRecord)
                .where(PolicyDecisionRecord.payment_authorization_id == auth.id)
                .order_by(PolicyDecisionRecord.evaluated_at)
            )
        ]
        step(
            "Policy decision trail (database)",
            SYSTEM,
            decisions=trail,
            order_db_status=s.get(PaymentOrder, order.id).status.value,
        )
        status = "completed"
        return 0
    except PayPalAPIError as exc:
        step("Stopped: PayPal rejected a request", SYSTEM, paypal=exc.to_record())
        return 1
    finally:
        out.write_text(
            json.dumps(
                {
                    "kind": "p06-governance-demo",
                    "environment": "paypal-sandbox",
                    "status": status,
                    "started_at": started.isoformat(),
                    "finished_at": datetime.now(UTC).isoformat(),
                    "actors": {
                        "HUMAN": HUMAN,
                        "SCRIPTED": "demo accounts driven by this script",
                        "SYSTEM": SYSTEM,
                    },
                    "simulated": [
                        "the capture response was discarded client-side after a real POST"
                    ],
                    "log": LOG,
                },
                indent=2,
                default=str,
            )
        )
        print(f"\ntranscript ({status}): {out.relative_to(REPO)}")


if __name__ == "__main__":
    raise SystemExit(main())
