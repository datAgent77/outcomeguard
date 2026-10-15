"""P03 agreement domain: lifecycle, versioning, locking, RBAC, concurrency, audit."""

import threading
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from outcomeguard.auth import Forbidden, load_principal
from outcomeguard.domain.criteria import CriterionInput, parse_spec
from outcomeguard.models import (
    AgreementStatus,
    AgreementVersion,
    Approval,
    AuditEvent,
    MerchantAccount,
    Organization,
    OrgKind,
    PaymentEnvironment,
    VersionStatus,
)
from outcomeguard.models.audit import verify_chain
from outcomeguard.money import Money
from outcomeguard.services.agreements import AgreementError, AgreementService, DraftContent
from outcomeguard.services.payments import PaymentRuleViolation

DEADLINE = datetime.now(UTC) + timedelta(days=14)


def content(**kw) -> DraftContent:
    base = dict(
        title="Spring campaign landing page",
        description="One-page site for the spring campaign",
        requirements_text=(
            "Build a responsive landing page for $500. The contact form must work, mobile "
            "layout must pass verification, and Lighthouse performance score must be at least 90."
        ),
        amount=Money(50000, "USD"),
        delivery_deadline=DEADLINE,
    )
    base.update(kw)
    return DraftContent(**base)


@pytest.fixture
def agreements(session) -> AgreementService:
    return AgreementService(session=session)


@pytest.fixture
def draft(agreements, ids, who):
    return agreements.create(
        who("alice"),
        buyer_organization_id=ids.buyer_org_id,
        merchant_account_id=ids.merchant.id,
        content=content(),
        use_template=True,
    )


def v(agreement, n=1) -> AgreementVersion:
    return next(x for x in agreement.versions if x.version_number == n)


def proposed(agreements, who, draft):
    ver = v(draft)
    return agreements.propose(who("alice"), draft.id, 1, expected_revision=ver.revision)


def locked(agreements, who, draft):
    p = proposed(agreements, who, draft)
    return agreements.approve(who("bob"), draft.id, 1, content_hash_seen=p.content_hash)


def code(ei) -> str:
    return ei.value.code


# ----------------------------------------------------------------- creation


def test_create_with_landing_page_template(draft, session):
    ver = v(draft)
    assert draft.status is AgreementStatus.DRAFT
    assert ver.status is VersionStatus.DRAFT and ver.amount_minor == 50000
    kinds = [c.kind.value for c in ver.criteria]
    assert kinds == [
        "http_availability",
        "mobile_viewport",
        "contact_form",
        "lighthouse_performance",
    ]
    assert ver.criteria[3].spec["min_score"] == 90
    assert all(c.verification_method.value == "automated" for c in ver.criteria)
    assert session.scalar(select(AuditEvent).where(AuditEvent.action == "agreement.created"))


def test_only_buyer_admin_of_that_org_can_create(agreements, ids, who):
    for name in ("bob", "maya", "eve"):
        with pytest.raises(Forbidden):
            agreements.create(
                who(name),
                buyer_organization_id=ids.buyer_org_id,
                merchant_account_id=ids.merchant.id,
                content=content(),
                use_template=True,
            )


def test_merchant_org_cannot_be_buyer(agreements, ids, who, session):
    m_org = session.get(Organization, ids.merchant.organization_id)
    assert m_org.kind is OrgKind.MERCHANT
    with pytest.raises((Forbidden, AgreementError)):
        agreements.create(
            who("alice"),
            buyer_organization_id=m_org.id,
            merchant_account_id=ids.merchant.id,
            content=content(),
            use_template=True,
        )


@pytest.mark.parametrize(
    "spec",
    [
        {"kind": "lighthouse_performance", "min_score": 150},
        {"kind": "http_availability", "path": "http://169.254.169.254/latest"},
        {"kind": "http_availability", "path": "/", "shell": "rm -rf /"},
        {"kind": "contact_form", "fields": [], "success_text": "ok"},
        {
            "kind": "contact_form",
            "fields": [{"selector": "input", "value": "x"}],
            "success_text": "ok",
            "test_endpoint_ref": "https://attacker.example",
        },
        {"kind": "made_up"},
    ],
)
def test_criterion_specs_are_strictly_validated(spec):
    with pytest.raises(ValidationError):
        parse_spec(spec)


def test_manual_review_criterion_is_flagged(agreements, draft, who):
    ver = agreements.replace_criteria(
        who("alice"),
        draft.id,
        1,
        expected_revision=v(draft).revision,
        criteria=[
            CriterionInput(
                key="brand_tone",
                title="Copy matches brand tone",
                spec={"kind": "manual_review", "instructions": "Check tone"},
            )
        ],
    )
    assert ver.criteria[0].verification_method.value == "manual_review_required"


# --------------------------------------------------------- editing & revision


def test_update_draft_bumps_revision_and_stale_edit_rejected(agreements, draft, who):
    rev = v(draft).revision
    updated = agreements.update_draft(
        who("alice"), draft.id, 1, expected_revision=rev, changes={"title": "New title"}
    )
    assert updated.revision == rev + 1
    with pytest.raises(AgreementError) as ei:
        agreements.update_draft(
            who("alice"), draft.id, 1, expected_revision=rev, changes={"title": "Lost update"}
        )
    assert code(ei) == "STALE_REVISION"


def test_non_editable_fields_rejected(agreements, draft, who):
    with pytest.raises(AgreementError) as ei:
        agreements.update_draft(
            who("alice"),
            draft.id,
            1,
            expected_revision=v(draft).revision,
            changes={"status": "locked"},
        )
    assert code(ei) == "INVALID_FIELDS"


def test_concurrent_version_updates_one_wins(make_session, draft, ids):
    rev = v(draft).revision
    barrier = threading.Barrier(2)
    results: list[str] = []

    def worker(title: str):
        s = make_session()
        try:
            p = load_principal(s, ids.users["alice@acme.demo"])
            barrier.wait()
            AgreementService(session=s).update_draft(
                p, draft.id, 1, expected_revision=rev, changes={"title": title}
            )
            results.append("ok")
        except AgreementError as exc:
            results.append(exc.code)
        finally:
            s.close()

    ts = [threading.Thread(target=worker, args=(f"title {i}",)) for i in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(results) == ["STALE_REVISION", "ok"]


# ------------------------------------------------------- propose / approve


def test_approve_locks_version(agreements, draft, who, session):
    ver = locked(agreements, who, draft)
    session.expire_all()
    assert ver.status is VersionStatus.LOCKED and ver.locked_at
    agr = agreements.get(who("alice"), draft.id)
    assert agr.status is AgreementStatus.ACTIVE
    assert agr.current_locked_version_id == ver.id
    approval = session.scalar(select(Approval).where(Approval.subject_id == ver.id))
    assert approval.content_hash == ver.content_hash
    assert approval.approver_role == "buyer_approver"


def test_approval_must_quote_current_content_hash(agreements, draft, who):
    proposed(agreements, who, draft)
    with pytest.raises(AgreementError) as ei:
        agreements.approve(who("bob"), draft.id, 1, content_hash_seen="0" * 64)
    assert code(ei) == "CONTENT_CHANGED"


def test_withdraw_edit_repropose_invalidates_old_hash(agreements, draft, who):
    p = proposed(agreements, who, draft)
    old_hash = p.content_hash
    w = agreements.withdraw(who("alice"), draft.id, 1, expected_revision=p.revision)
    e = agreements.update_draft(
        who("alice"),
        draft.id,
        1,
        expected_revision=w.revision,
        changes={"amount_minor": 60000},
    )
    p2 = agreements.propose(who("alice"), draft.id, 1, expected_revision=e.revision)
    assert p2.content_hash != old_hash
    with pytest.raises(AgreementError) as ei:
        agreements.approve(who("bob"), draft.id, 1, content_hash_seen=old_hash)
    assert code(ei) == "CONTENT_CHANGED"


def test_duplicate_approval_rejected(agreements, draft, who):
    ver = locked(agreements, who, draft)
    for name in ("bob", "alice"):
        with pytest.raises(AgreementError) as ei:
            agreements.approve(who(name), draft.id, 1, content_hash_seen=ver.content_hash)
        assert code(ei) == "ALREADY_APPROVED"


def test_concurrent_approvals_one_wins(make_session, agreements, draft, who, ids):
    p = proposed(agreements, who, draft)
    barrier = threading.Barrier(2)
    results: list[str] = []

    def worker(email: str):
        s = make_session()
        try:
            pr = load_principal(s, ids.users[email])
            barrier.wait()
            AgreementService(session=s).approve(pr, draft.id, 1, content_hash_seen=p.content_hash)
            results.append("ok")
        except AgreementError as exc:
            results.append(exc.code)
        finally:
            s.close()

    ts = [threading.Thread(target=worker, args=(e,)) for e in ("bob@acme.demo", "alice@acme.demo")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(results) == ["ALREADY_APPROVED", "ok"]


@pytest.mark.parametrize(
    "action",
    ["approve_draft", "withdraw_draft", "propose_twice", "propose_without_criteria"],
)
def test_invalid_state_transitions(agreements, draft, who, action):
    a, ver = who("alice"), v(draft)
    with pytest.raises(AgreementError) as ei:
        if action == "approve_draft":
            agreements.approve(who("bob"), draft.id, 1, content_hash_seen="a" * 64)
        elif action == "withdraw_draft":
            agreements.withdraw(a, draft.id, 1, expected_revision=ver.revision)
        elif action == "propose_twice":
            p = agreements.propose(a, draft.id, 1, expected_revision=ver.revision)
            agreements.propose(a, draft.id, 1, expected_revision=p.revision)
        else:
            e = agreements.replace_criteria(
                a, draft.id, 1, expected_revision=ver.revision, criteria=[]
            )
            agreements.propose(a, draft.id, 1, expected_revision=e.revision)
    assert code(ei) in {"INVALID_TRANSITION", "NO_CRITERIA"}


def test_proposed_version_cannot_be_edited(agreements, draft, who):
    p = proposed(agreements, who, draft)
    with pytest.raises(AgreementError) as ei:
        agreements.update_draft(
            who("alice"), draft.id, 1, expected_revision=p.revision, changes={"title": "sneaky"}
        )
    assert code(ei) == "INVALID_TRANSITION"


# ------------------------------------------------------------ immutability


def test_locked_version_cannot_be_modified_via_service(agreements, draft, who):
    ver = locked(agreements, who, draft)
    for call in (
        lambda: agreements.update_draft(
            who("alice"), draft.id, 1, expected_revision=ver.revision, changes={"title": "x"}
        ),
        lambda: agreements.replace_criteria(
            who("alice"), draft.id, 1, expected_revision=ver.revision, criteria=[]
        ),
        lambda: agreements.discard(who("alice"), draft.id, 1, expected_revision=ver.revision),
    ):
        with pytest.raises(AgreementError) as ei:
            call()
        assert code(ei) in {"VERSION_LOCKED", "INVALID_TRANSITION"}


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE agreement_versions SET amount_minor = 1 WHERE id = :vid",
        "UPDATE agreement_versions SET status = 'draft' WHERE id = :vid",
        "DELETE FROM agreement_versions WHERE id = :vid",
        "UPDATE acceptance_criteria SET spec = '{}' WHERE agreement_version_id = :vid",
        "DELETE FROM acceptance_criteria WHERE agreement_version_id = :vid",
        "INSERT INTO acceptance_criteria (id, agreement_version_id, position, key, title, kind,"
        " verification_method, blocking, source, spec) VALUES ('x', :vid, 99, 'extra', 'x',"
        " 'manual_review', 'manual_review_required', true, 'buyer', '{}')",
        "UPDATE approvals SET note = 'edited' WHERE subject_id = :vid",
        "DELETE FROM approvals WHERE subject_id = :vid",
    ],
)
def test_database_refuses_to_rewrite_locked_history(agreements, draft, who, session, sql):
    ver = locked(agreements, who, draft)
    with pytest.raises(DBAPIError, match="immutable|cannot|append-only"):
        session.execute(text(sql), {"vid": ver.id})
        session.flush()
    session.rollback()


# ---------------------------------------------------------------- versions


def test_amendment_creates_new_version_and_supersedes_old(agreements, draft, who, session):
    v1 = locked(agreements, who, draft)
    v2 = agreements.new_version(who("alice"), draft.id)
    assert v2.version_number == 2 and v2.status is VersionStatus.DRAFT
    assert v2.supersedes_version_id == v1.id
    assert [c.key for c in v2.criteria] == [c.key for c in v1.criteria]
    with pytest.raises(AgreementError) as ei:
        agreements.new_version(who("alice"), draft.id)
    assert code(ei) == "OPEN_VERSION_EXISTS"

    e = agreements.update_draft(
        who("alice"), draft.id, 2, expected_revision=v2.revision, changes={"amount_minor": 55000}
    )
    p = agreements.propose(who("alice"), draft.id, 2, expected_revision=e.revision)
    agreements.approve(who("bob"), draft.id, 2, content_hash_seen=p.content_hash)

    session.expire_all()
    agr = agreements.get(who("alice"), draft.id)
    old, new = v(agr, 1), v(agr, 2)
    assert old.status is VersionStatus.SUPERSEDED and old.amount_minor == 50000
    assert new.status is VersionStatus.LOCKED
    assert agr.current_locked_version_id == new.id


def test_cannot_amend_before_first_lock(agreements, draft, who):
    with pytest.raises(AgreementError) as ei:
        agreements.new_version(who("alice"), draft.id)
    assert code(ei) in {"OPEN_VERSION_EXISTS", "NOT_LOCKED"}


# -------------------------------------------------------------------- RBAC


def test_cross_organization_access_is_invisible(agreements, draft, who):
    eve = who("eve")
    assert agreements.list_visible(eve) == []
    for call in (
        lambda: agreements.get(eve, draft.id),
        lambda: agreements.update_draft(
            eve, draft.id, 1, expected_revision=1, changes={"title": "x"}
        ),
        lambda: agreements.approve(eve, draft.id, 1, content_hash_seen="a" * 64),
        lambda: agreements.new_version(eve, draft.id),
    ):
        with pytest.raises(AgreementError) as ei:
            call()
        assert ei.value.http_status == 404


def test_merchant_can_read_but_not_change(agreements, draft, who):
    maya = who("maya")
    assert agreements.get(maya, draft.id).id == draft.id
    assert [a.id for a in agreements.list_visible(maya)] == [draft.id]
    with pytest.raises(Forbidden) as ei:
        agreements.update_draft(
            maya, draft.id, 1, expected_revision=v(draft).revision, changes={"title": "x"}
        )
    assert not ei.value.hide
    p = proposed(agreements, who, draft)
    with pytest.raises(Forbidden):
        agreements.approve(maya, draft.id, 1, content_hash_seen=p.content_hash)


def test_approver_cannot_edit(agreements, draft, who):
    with pytest.raises(Forbidden):
        agreements.update_draft(
            who("bob"), draft.id, 1, expected_revision=v(draft).revision, changes={"title": "x"}
        )


def test_revoked_role_takes_effect_immediately(agreements, draft, who, session, ids):
    session.execute(
        text("DELETE FROM organization_memberships WHERE user_id = :u"),
        {"u": ids.users["bob@acme.demo"]},
    )
    session.commit()
    p = proposed(agreements, who, draft)
    with pytest.raises(AgreementError) as ei:
        agreements.approve(who("bob"), draft.id, 1, content_hash_seen=p.content_hash)
    assert ei.value.http_status == 404


# ------------------------------------------------------------------- audit


def test_every_material_change_is_audited(agreements, draft, who, session):
    ver = v(draft)
    e = agreements.update_draft(
        who("alice"), draft.id, 1, expected_revision=ver.revision, changes={"title": "T2"}
    )
    p = agreements.propose(who("alice"), draft.id, 1, expected_revision=e.revision)
    agreements.approve(who("bob"), draft.id, 1, content_hash_seen=p.content_hash)
    actions = session.scalars(select(AuditEvent.action).order_by(AuditEvent.seq)).all()
    assert actions == [
        "agreement.created",
        "agreement.version.updated",
        "agreement.version.proposed",
        "agreement.version.approved_and_locked",
    ]
    actors = session.scalars(select(AuditEvent.actor).order_by(AuditEvent.seq)).all()
    assert actors[-1] == "user:bob@acme.demo"
    assert verify_chain(session) == (True, None)


def test_rejected_attempts_leave_no_partial_changes(agreements, draft, who, session):
    before = session.scalar(select(AuditEvent.seq).order_by(AuditEvent.seq.desc()).limit(1))
    with pytest.raises(Forbidden):
        agreements.update_draft(
            who("bob"), draft.id, 1, expected_revision=v(draft).revision, changes={"title": "x"}
        )
    after = session.scalar(select(AuditEvent.seq).order_by(AuditEvent.seq.desc()).limit(1))
    assert before == after
    session.expire_all()
    assert v(agreements.get(who("alice"), draft.id)).title == "Spring campaign landing page"


# --------------------------------------------------- payment domain binding


def test_payment_requires_locked_agreement_version(agreements, draft, who, svc):
    with pytest.raises(PaymentRuleViolation) as ei:
        svc.create_order_for_agreement(v(draft), actor="alice")
    assert code(ei) == "AGREEMENT_NOT_LOCKED"
    ver = locked(agreements, who, draft)
    order = svc.create_order_for_agreement(ver, actor="alice")
    assert order.agreement_version_id == ver.id
    assert (order.amount_minor, order.currency) == (50000, "USD")


def test_payment_amount_and_merchant_must_match_agreement(agreements, draft, who, svc, session):
    ver = locked(agreements, who, draft)
    merchant = session.get(MerchantAccount, draft.merchant_account_id)
    with pytest.raises(PaymentRuleViolation) as ei:
        svc.create_order(
            merchant=merchant,
            amount=Money(100, "USD"),
            description="x",
            actor="t",
            agreement_version=ver,
        )
    assert code(ei) == "AMOUNT_MISMATCH"
    other_org = Organization(name="Rogue agency", kind=OrgKind.MERCHANT)
    session.add(other_org)
    session.flush()
    rogue = MerchantAccount(
        organization_id=other_org.id, display_name="Rogue", environment=PaymentEnvironment.SIMULATED
    )
    session.add(rogue)
    session.commit()
    with pytest.raises(PaymentRuleViolation) as ei:
        svc.create_order(
            merchant=rogue,
            amount=Money(50000, "USD"),
            description="x",
            actor="t",
            agreement_version=ver,
        )
    assert code(ei) == "MERCHANT_MISMATCH"
