"""Pure policy engine (no DB) and authentication (password hashing, sessions, lockout)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from outcomeguard.auth import (
    AuthError,
    authenticate,
    hash_password,
    login,
    logout,
    set_password,
    verify_password,
)
from outcomeguard.domain.policy import (
    CriterionIn,
    Decision,
    PolicyInputs,
    ResultIn,
    ReviewIn,
    evaluate,
)
from outcomeguard.models import AuthSession, User

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def base(**kw) -> PolicyInputs:
    i = PolicyInputs(
        agreement_id="ag",
        agreement_current_locked_version_id="v1",
        version_id="v1",
        version_status="locked",
        version_content_hash="h1",
        version_amount_minor=50000,
        version_currency="USD",
        agreement_merchant_account_id="m",
        order_agreement_version_id="v1",
        order_amount_minor=50000,
        order_currency="USD",
        order_merchant_account_id="m",
        merchant_paypal_id="PAYEE",
        authorization_id="a",
        authorization_status="authorized",
        authorization_amount_minor=50000,
        authorization_currency="USD",
        authorization_payee="PAYEE",
        authorization_expires_at=(NOW + timedelta(days=20)).isoformat(),
        capture_status=None,
        latest_submission_id="s1",
        run_id="r1",
        run_submission_id="s1",
        run_status="completed",
        run_verdict="needs_manual_review",
        run_version_id="v1",
        run_content_hash="h1",
        evidence_snapshot_sha256="snap",
        criteria=(
            CriterionIn("c1", "form", "contact_form", True),
            CriterionIn("c2", "a11y", "manual_review", True),
        ),
        results=(ResultIn("c1", "pass"), ResultIn("c2", "manual_review_required")),
        reviews=(ReviewIn("c2", "r1", "v1", "h1", "snap", "satisfied", True, False),),
    )
    return replace(i, **kw)


def codes(i):
    return [r.code for r in evaluate(i, NOW).reasons]


def test_all_conditions_met_is_eligible_not_captured():
    r = evaluate(base(), NOW)
    assert r.decision is Decision.ELIGIBLE_FOR_APPROVAL and r.reasons == ()


@pytest.mark.parametrize(
    "change,code",
    [
        ({"order_agreement_version_id": None}, "PAYMENT_NOT_BOUND_TO_AGREEMENT"),
        ({"version_status": "superseded"}, "AGREEMENT_NOT_LOCKED"),
        ({"agreement_current_locked_version_id": "v2"}, "AGREEMENT_SUPERSEDED"),
        ({"order_amount_minor": 40000}, "AMOUNT_MISMATCH"),
        ({"authorization_currency": "EUR"}, "AMOUNT_MISMATCH"),
        ({"order_merchant_account_id": "other"}, "MERCHANT_MISMATCH"),
        ({"authorization_payee": "ATTACKER"}, "MERCHANT_MISMATCH"),
        ({"authorization_status": "voided"}, "AUTH_NOT_CAPTURABLE"),
        ({"authorization_expires_at": (NOW + timedelta(minutes=10)).isoformat()}, "AUTH_EXPIRED"),
        ({"authorization_expires_at": None}, "AUTH_EXPIRY_UNKNOWN"),
        ({"capture_status": "completed"}, "ALREADY_CAPTURED"),
        ({"capture_status": "unknown"}, "CAPTURE_IN_PROGRESS"),
        ({"run_id": None}, "NO_VERIFICATION_RUN"),
        ({"latest_submission_id": "s2"}, "STALE_EVIDENCE"),
        ({"run_content_hash": "old"}, "RUN_VERSION_MISMATCH"),
        (
            {"results": (ResultIn("c1", "fail"), ResultIn("c2", "manual_review_required"))},
            "BLOCKING_CRITERION_FAILED",
        ),
        (
            {"results": (ResultIn("c1", "error"), ResultIn("c2", "manual_review_required"))},
            "VERIFICATION_INCONCLUSIVE",
        ),
        ({"results": (ResultIn("c2", "manual_review_required"),)}, "CRITERION_NOT_EVALUATED"),
        ({"criteria": (CriterionIn("c1", "form", "contact_form", False),)}, "NO_BLOCKING_CRITERIA"),
    ],
)
def test_each_condition_blocks(change, code):
    i = base(**change)
    r = evaluate(i, NOW)
    assert r.decision is Decision.BLOCKED and code in codes(i)


@pytest.mark.parametrize(
    "review",
    [
        None,
        ReviewIn("c2", "r0", "v1", "h1", "snap", "satisfied", True, False),  # other run
        ReviewIn("c2", "r1", "v0", "h1", "snap", "satisfied", True, False),  # other version
        ReviewIn("c2", "r1", "v1", "h1", "old-snap", "satisfied", True, False),  # stale evidence
        ReviewIn("c2", "r1", "v1", "h1", "snap", "satisfied", False, False),  # service account
        ReviewIn("c2", "r1", "v1", "h1", "snap", "satisfied", True, True),  # provider
    ],
)
def test_manual_review_only_counts_when_bound_and_human(review):
    i = base(reviews=(review,) if review else ())
    r = evaluate(i, NOW)
    assert r.decision is Decision.NEEDS_REVIEW and codes(i) == ["MANUAL_REVIEW_PENDING"]


def test_rejected_review_blocks():
    i = base(reviews=(ReviewIn("c2", "r1", "v1", "h1", "snap", "not_satisfied", True, False),))
    assert evaluate(i, NOW).decision is Decision.BLOCKED and codes(i) == ["MANUAL_REVIEW_REJECTED"]


def test_inputs_hash_changes_with_facts_but_not_with_clock():
    a, b = base(), base(latest_submission_id="s1")
    assert a.sha256() == b.sha256()
    assert evaluate(a, NOW).inputs_sha256 == evaluate(a, NOW + timedelta(days=1)).inputs_sha256
    assert base(capture_status="failed").sha256() != a.sha256()
    assert base(evidence_snapshot_sha256="other").sha256() != a.sha256()


# =================================================================== auth


def test_password_hashing():
    h = hash_password("correct horse battery staple")
    assert h.startswith("scrypt$") and "correct" not in h
    assert verify_password("correct horse battery staple", h)
    assert not verify_password("wrong horse battery staple", h)
    assert not verify_password("x", "garbage")
    with pytest.raises(ValueError):
        hash_password("short")


@pytest.fixture
def alice(session, ids):
    u = session.scalar(select(User).where(User.email == "alice@acme.demo"))
    set_password(session, u, "correct horse battery staple")
    session.commit()
    return u


def test_login_session_and_logout(session, alice):
    token = login(session, "alice@acme.demo", "correct horse battery staple")
    assert authenticate(session, token) == alice.id
    stored = session.scalar(select(AuthSession.token_sha256))
    assert token not in stored and len(stored) == 64
    logout(session, token)
    with pytest.raises(AuthError):
        authenticate(session, token)


def test_wrong_password_and_unknown_user_are_indistinguishable(session, alice):
    for email, pw in (("alice@acme.demo", "nope nope nope"), ("ghost@acme.demo", "whatever pw")):
        with pytest.raises(AuthError, match="invalid credentials"):
            login(session, email, pw)


def test_lockout_after_failures(session, alice):
    for _ in range(5):
        with pytest.raises(AuthError):
            login(session, "alice@acme.demo", "bad password!!")
    with pytest.raises(AuthError):  # correct password, but locked
        login(session, "alice@acme.demo", "correct horse battery staple")


def test_expired_session_and_password_change_revoke(session, alice):
    token = login(session, "alice@acme.demo", "correct horse battery staple")
    row = session.scalar(select(AuthSession))
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()
    with pytest.raises(AuthError):
        authenticate(session, token)
    token2 = login(session, "alice@acme.demo", "correct horse battery staple")
    set_password(session, session.get(User, alice.id), "another long password")
    session.commit()
    with pytest.raises(AuthError):
        authenticate(session, token2)


def test_http_login_and_me(make_session, alice):
    from outcomeguard.main import create_app

    c = TestClient(create_app())
    assert (
        c.post("/auth/login", json={"email": "alice@acme.demo", "password": "x"}).status_code == 401
    )
    tok = c.post(
        "/auth/login", json={"email": "alice@acme.demo", "password": "correct horse battery staple"}
    ).json()["token"]
    me = c.get("/me", headers={"Authorization": f"Bearer {tok}"}).json()
    assert me["email"] == "alice@acme.demo" and me["kind"] == "human"
    assert c.post("/auth/logout", headers={"Authorization": f"Bearer {tok}"}).status_code == 204
    assert c.get("/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401
