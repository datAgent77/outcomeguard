"""Builders for P06 governance tests (mocked PayPal via the scripted fake gateway; real
verification measurements are replaced by persisted measurement dicts)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from outcomeguard.domain.criteria import CriterionInput, landing_page_template
from outcomeguard.models import (
    AcceptanceCriterion,
    FormSinkReceipt,
    PaymentAuthorization,
    VerificationRun,
)
from outcomeguard.money import Money
from outcomeguard.services.agreements import AgreementService, DraftContent
from outcomeguard.verification.service import Orchestrator, claim_job, submit_evidence
from outcomeguard.verification.settings import VerifierSettings

REQ = (
    "Build a mobile-friendly landing page for $500. The contact form must work, the website "
    "must be accessible and Lighthouse performance must score at least 90."
)


def locked_agreement_with_manual(session, ids, who, amount=50000):
    crits = landing_page_template()
    crits[0] = crits[0].model_copy(
        update={"spec": crits[0].spec.model_copy(update={"require_https": False})}
    )
    crits.append(
        CriterionInput(
            key="website_accessible",
            title="Website is accessible",
            spec={"kind": "manual_review", "instructions": "Check WCAG 2.2 AA"},
        )
    )
    svc = AgreementService(session=session)
    a = svc.create(
        who("alice"),
        buyer_organization_id=ids.buyer_org_id,
        merchant_account_id=ids.merchant.id,
        content=DraftContent(
            title="Landing page",
            description="",
            requirements_text=REQ,
            amount=Money(amount, "USD"),
            delivery_deadline=datetime.now(UTC) + timedelta(days=7),
        ),
        criteria=crits,
    )
    p = svc.propose(who("alice"), a.id, 1, expected_revision=a.versions[0].revision)
    svc.approve(who("bob"), a.id, 1, content_hash_seen=p.content_hash)
    session.expire_all()
    return svc.get(who("alice"), a.id)


def authorized_payment(session, svc, gateway, agreement) -> PaymentAuthorization:
    from outcomeguard.models import AgreementVersion

    version = session.get(AgreementVersion, agreement.current_locked_version_id)
    order = svc.create_order_for_agreement(version, actor="alice")
    gateway.simulate_buyer_approval(order.paypal_order_id)
    return svc.authorize_order(order.id, actor="alice")


def completed_run(session, who, agreement, tmp_path: Path, *, form_ok: bool) -> VerificationRun:
    """A completed run for the agreement's locked version: every automated check passes,
    except the contact form when `form_ok` is False; the manual criterion stays manual."""
    submit_evidence(session, who("maya"), agreement, delivery_url="https://landing.example/")
    job = claim_job(session, "w", 600)
    assert job is not None
    by_kind = {
        c.kind.value: c.id
        for c in session.scalars(
            select(AcceptanceCriterion).where(
                AcceptanceCriterion.agreement_version_id == agreement.current_locked_version_id
            )
        )
    }
    m = {
        by_kind["http_availability"]: {
            "chain": [{"url": "http://landing.example/", "status": 200}]
        },
        by_kind["mobile_viewport"]: {
            "scroll_width": 390,
            "viewport_width": 390,
            "selectors_visible": {},
        },
        by_kind["contact_form"]: {
            "form_found": True,
            "fields_missing": [],
            "sink_requests": [{"status": 201}] if form_ok else [],
            "success_text_seen": form_ok,
            "prohibited_requests": [],
        },
        by_kind["lighthouse_performance"]: {"scores": [100], "band": 5},
    }
    if form_ok:
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
    tmp_path.mkdir(parents=True, exist_ok=True)
    run = Orchestrator(session, VerifierSettings(evidence_dir=str(tmp_path)), "w")._persist(
        job.id, {"measurements": m, "artifacts": []}, tmp_path
    )
    session.expire_all()
    return session.get(VerificationRun, run.id)


def manual_criterion_id(session, agreement) -> str:
    return session.scalar(
        select(AcceptanceCriterion.id).where(
            AcceptanceCriterion.agreement_version_id == agreement.current_locked_version_id,
            AcceptanceCriterion.key == "website_accessible",
        )
    )
