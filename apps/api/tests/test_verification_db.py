"""Verification persistence: submission rules, queue concurrency/leases, immutability,
the controlled form sink, and credential isolation of the verifier."""

import threading
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from outcomeguard.auth import Forbidden
from outcomeguard.domain.verdict import RunVerdict
from outcomeguard.models import (
    FormSinkReceipt,
    JobState,
    RunStatus,
    VerificationJob,
    VerificationRun,
)
from outcomeguard.models.base import utcnow
from outcomeguard.verification import sinktoken
from outcomeguard.verification.service import (
    Orchestrator,
    VerificationError,
    claim_job,
    is_verified_pass,
    submit_evidence,
)
from outcomeguard.verification.settings import VerifierSettings
from tests.conftest import TEST_DB

URL = "https://landing.example/"


def submit(session, who, agreement, url=URL):
    return submit_evidence(session, who("maya"), agreement, delivery_url=url)


# ------------------------------------------------------------------ submission


def test_only_provider_of_contracted_merchant_may_submit(session, who, locked_agreement):
    for name in ("alice", "bob", "eve"):
        with pytest.raises(Forbidden):
            submit_evidence(session, who(name), locked_agreement, delivery_url=URL)
    run = submit(session, who, locked_agreement)
    assert run.status is RunStatus.QUEUED
    assert run.agreement_version_id == locked_agreement.current_locked_version_id


def test_submission_requires_locked_agreement(session, who, ids):
    from datetime import UTC, datetime

    from outcomeguard.money import Money
    from outcomeguard.services.agreements import AgreementService, DraftContent

    a = AgreementService(session=session).create(
        who("alice"),
        buyer_organization_id=ids.buyer_org_id,
        merchant_account_id=ids.merchant.id,
        content=DraftContent(
            title="t",
            description="",
            requirements_text="r",
            amount=Money(100, "USD"),
            delivery_deadline=datetime.now(UTC) + timedelta(days=3),
        ),
        use_template=True,
    )
    with pytest.raises(VerificationError) as ei:
        submit(session, who, a)
    assert ei.value.code == "AGREEMENT_NOT_LOCKED"


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "javascript:alert(1)", "https://u:p@x.example/", "gopher://x/"]
)
def test_unsafe_delivery_urls_rejected_at_submission(session, who, locked_agreement, url):
    with pytest.raises(VerificationError):
        submit(session, who, locked_agreement, url)


# ----------------------------------------------------------------------- queue


def test_concurrent_workers_claim_each_job_once(make_session, session, who, locked_agreement):
    for _ in range(6):
        submit(session, who, locked_agreement)
    claimed: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(6)

    def worker(i):
        s = make_session()
        barrier.wait()
        while True:
            job = claim_job(s, f"w{i}", 600)
            if job is None:
                break
            with lock:
                claimed.append(job.id)
        s.close()

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(claimed) == 6 and len(set(claimed)) == 6


def test_expired_lease_is_reclaimed_then_gives_up_inconclusive(session, who, locked_agreement):
    run = submit(session, who, locked_agreement)
    for attempt in (1, 2):
        job = claim_job(session, f"crashy-{attempt}", 600)
        assert job is not None and job.attempts == attempt
        session.execute(text("UPDATE verification_jobs SET lease_until = now() - interval '1 s'"))
        session.commit()
    assert claim_job(session, "w3", 600) is None  # max attempts exhausted
    session.expire_all()
    run = session.get(VerificationRun, run.id)
    assert run.status is RunStatus.ERRORED and run.verdict is RunVerdict.INCONCLUSIVE
    assert not is_verified_pass(session, run.id)


def test_runner_failure_never_produces_pass(session, who, locked_agreement, tmp_path):
    run = submit(session, who, locked_agreement)
    orch = Orchestrator(
        session, VerifierSettings(database_url=TEST_DB, evidence_dir=str(tmp_path)), "w1"
    )
    orch.invoke_runner = lambda spec: (_ for _ in ()).throw(RuntimeError("chromium crashed"))
    for _ in range(2):
        job = claim_job(session, "w1", 600)
        assert job is not None
        orch.process(job)
        session.execute(text("UPDATE verification_jobs SET available_at = now()"))  # skip backoff
        session.commit()
    session.expire_all()
    run = session.get(VerificationRun, run.id)
    assert run.status is RunStatus.ERRORED and run.verdict is RunVerdict.INCONCLUSIVE
    assert "chromium crashed" in run.error


def test_lost_lease_cannot_persist(session, who, locked_agreement, tmp_path):
    submit(session, who, locked_agreement)
    job = claim_job(session, "slow", 600)
    session.execute(text("UPDATE verification_jobs SET lease_owner = 'other'"))
    session.commit()
    orch = Orchestrator(
        session, VerifierSettings(database_url=TEST_DB, evidence_dir=str(tmp_path)), "slow"
    )
    with pytest.raises(VerificationError) as ei:
        orch._persist(job.id, {"measurements": {}, "artifacts": []}, tmp_path)
    assert ei.value.code == "LEASE_LOST"


# --------------------------------------------------------------- immutability


def complete_run(session, who, agreement, tmp_path):
    submit(session, who, agreement)
    job = claim_job(session, "w", 600)
    orch = Orchestrator(
        session, VerifierSettings(database_url=TEST_DB, evidence_dir=str(tmp_path)), "w"
    )
    (tmp_path / "shot.png").write_bytes(b"\x89PNG fake-but-real-bytes")
    return orch._persist(
        job.id,
        {
            "measurements": {},
            "artifacts": [
                {
                    "label": "shot.png",
                    "kind": "screenshot",
                    "file": "shot.png",
                    "content_type": "image/png",
                    "criterion_id": None,
                }
            ],
        },
        tmp_path,
    )


def test_unmeasured_criteria_are_not_run_and_verdict_inconclusive(
    session, who, locked_agreement, tmp_path
):
    run = complete_run(session, who, locked_agreement, tmp_path)
    assert run.status is RunStatus.COMPLETED and run.verdict is RunVerdict.INCONCLUSIVE
    assert {r.outcome.value for r in run.results} == {"not_run"}
    assert not is_verified_pass(session, run.id)


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE verification_runs SET verdict = 'pass' WHERE id = :rid",
        "UPDATE verification_runs SET status = 'running' WHERE id = :rid",
        "DELETE FROM verification_runs WHERE id = :rid",
        "UPDATE verification_results SET outcome = 'pass' WHERE run_id = :rid",
        "DELETE FROM verification_results WHERE run_id = :rid",
        "UPDATE evidence_artifacts SET sha256 = repeat('0', 64) WHERE run_id = :rid",
        "DELETE FROM evidence_artifacts WHERE run_id = :rid",
        "UPDATE evidence_submissions SET delivery_url = 'https://other/'",
        "INSERT INTO verification_results (id, run_id, criterion_id, position, criterion_key, kind,"
        " blocking, outcome, reason_code, measured, expected, artifact_ids) SELECT 'x', :rid, id,"
        " 99, 'k', 'k', true, 'pass', 'OK', '{}', '{}', '[]' FROM acceptance_criteria LIMIT 1",
    ],
)
def test_completed_results_cannot_be_silently_modified(
    session, who, locked_agreement, tmp_path, sql
):
    run = complete_run(session, who, locked_agreement, tmp_path)
    with pytest.raises(DBAPIError, match="immutable|append-only|only be added|cannot"):
        session.execute(text(sql), {"rid": run.id})
        session.flush()
    session.rollback()


def test_pass_verdict_requires_completed_run_at_db_level(session, who, locked_agreement):
    run = submit(session, who, locked_agreement)
    with pytest.raises(DBAPIError, match="PASS verdict requires"):
        session.execute(
            text("UPDATE verification_runs SET verdict='pass' WHERE id=:r"), {"r": run.id}
        )
        session.flush()
    session.rollback()


def test_artifacts_are_hash_verified(session, who, locked_agreement, tmp_path):
    from outcomeguard.models import EvidenceArtifact
    from outcomeguard.verification.store import EvidenceIntegrityError, EvidenceStore

    run = complete_run(session, who, locked_agreement, tmp_path)
    art = session.scalar(select(EvidenceArtifact).where(EvidenceArtifact.run_id == run.id))
    store = EvidenceStore(tmp_path)
    assert store.get(art.sha256) == b"\x89PNG fake-but-real-bytes"
    (tmp_path / art.sha256[:2] / art.sha256).write_bytes(b"tampered")
    with pytest.raises(EvidenceIntegrityError):
        store.get(art.sha256)


# ------------------------------------------------------------------------ sink


@pytest.fixture
def api_client(make_session):
    from outcomeguard.main import create_app

    return TestClient(create_app())


def _running_run(session, who, agreement):
    run = submit(session, who, agreement)
    claim_job(session, "w", 600)
    return run


FORM = "name=Verifier&email=verifier%40example.com&message=hello"
CT = {"content-type": "application/x-www-form-urlencoded"}


def test_sink_rejects_requests_without_valid_run_token(api_client, session, who, locked_agreement):
    run = _running_run(session, who, locked_agreement)
    good = sinktoken.issue("test-sink-secret", run.id, "default")
    cases = [
        {},  # no token
        {"x-outcomeguard-sink-token": "garbage"},
        {"x-outcomeguard-sink-token": sinktoken.issue("wrong-secret", run.id, "default")},
        {"x-outcomeguard-sink-token": sinktoken.issue("test-sink-secret", run.id, "other")},
        {
            "x-outcomeguard-sink-token": sinktoken.issue(
                "test-sink-secret", run.id, "default", ttl=-5
            )
        },
    ]
    for h in cases:
        assert (
            api_client.post("/sinks/default", content=FORM, headers={**CT, **h}).status_code == 403
        )
    assert (
        api_client.post(
            "/sinks/unknown", content=FORM, headers={**CT, "x-outcomeguard-sink-token": good}
        ).status_code
        == 403
    )
    assert session.scalar(select(FormSinkReceipt)) is None


def test_sink_stores_field_names_and_hash_only(api_client, session, who, locked_agreement):
    run = _running_run(session, who, locked_agreement)
    tok = sinktoken.issue("test-sink-secret", run.id, "default")
    r = api_client.post(
        "/sinks/default",
        content=FORM,
        headers={**CT, "x-outcomeguard-sink-token": tok, "origin": "http://site"},
    )
    assert r.status_code == 201 and r.headers["access-control-allow-origin"] == "*"
    rec = session.scalar(select(FormSinkReceipt))
    assert rec.field_names == ["email", "message", "name"]
    assert "verifier" not in str(rec.__dict__).lower().replace("verification", "")


def test_sink_limits(api_client, session, who, locked_agreement):
    run = _running_run(session, who, locked_agreement)
    h = {**CT, "x-outcomeguard-sink-token": sinktoken.issue("test-sink-secret", run.id, "default")}
    assert (
        api_client.post("/sinks/default", content="x=" + "a" * 9000, headers=h).status_code == 413
    )
    assert api_client.post("/sinks/default", content="a=&b=", headers=h).status_code == 422
    assert (
        api_client.post(
            "/sinks/default", content="<xml/>", headers={**h, "content-type": "text/xml"}
        ).status_code
        == 415
    )
    codes = [
        api_client.post("/sinks/default", content=FORM, headers=h).status_code for _ in range(7)
    ]
    assert codes.count(201) == 5 and codes[-1] == 429


def test_sink_refuses_runs_that_are_not_running(api_client, session, who, locked_agreement):
    run = submit(session, who, locked_agreement)  # queued, not running
    tok = sinktoken.issue("test-sink-secret", run.id, "default")
    r = api_client.post(
        "/sinks/default", content=FORM, headers={**CT, "x-outcomeguard-sink-token": tok}
    )
    assert r.status_code == 403


# -------------------------------------------------------- credential isolation


def test_verifier_settings_never_load_paypal_credentials(monkeypatch):
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "should-not-load")
    s = VerifierSettings(database_url=TEST_DB)
    assert not any("paypal" in k.lower() for k in s.model_dump())


def test_worker_refuses_paypal_variables(monkeypatch):
    from outcomeguard.verification.settings import assert_no_paypal_credentials

    monkeypatch.setenv("PAYPAL_CLIENT_ID", "x")
    with pytest.raises(SystemExit):
        assert_no_paypal_credentials()


def test_runner_subprocess_gets_scrubbed_environment(
    monkeypatch, session, who, locked_agreement, tmp_path
):
    import subprocess

    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "leak-me")
    monkeypatch.setenv("DATABASE_URL", "postgresql://secret")
    captured = {}

    def fake_run(*a, **kw):
        captured.update(kw["env"])
        return subprocess.CompletedProcess(
            a, 0, stdout='{"measurements":{},"artifacts":[]}', stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    orch = Orchestrator(
        session, VerifierSettings(database_url=TEST_DB, evidence_dir=str(tmp_path)), "w"
    )
    orch.invoke_runner({"x": 1})
    assert "PAYPAL_CLIENT_SECRET" not in captured and "DATABASE_URL" not in captured
    assert set(captured) <= {
        "PATH",
        "HOME",
        "LANG",
        "TMPDIR",
        "PLAYWRIGHT_BROWSERS_PATH",
        "APP_ENV",
        "VERIFIER_DEV_ALLOW",
        "VERIFIER_CHROMIUM_SANDBOX",
    }


def test_job_state_after_success_is_done(session, who, locked_agreement, tmp_path):
    complete_run(session, who, locked_agreement, tmp_path)
    job = session.scalar(select(VerificationJob))
    assert job.state is JobState.DONE and job.lease_until is None
    assert utcnow() > job.created_at


def test_remote_runner_client_writes_inline_artifacts_locally(session, tmp_path):
    import base64

    import httpx
    import respx

    s = VerifierSettings(
        database_url=TEST_DB, evidence_dir=str(tmp_path), verifier_runner_url="http://runner:7000"
    )
    orch = Orchestrator(session, s, "w")
    payload = {
        "measurements": {},
        "artifacts": [
            {
                "label": "x.png",
                "kind": "screenshot",
                "file": "../../etc/evil.png",
                "content_type": "image/png",
                "criterion_id": None,
                "data_b64": base64.b64encode(b"img").decode(),
            }
        ],
    }
    with respx.mock:
        route = respx.post("http://runner:7000/run").mock(
            return_value=httpx.Response(200, json=payload)
        )
        out = orch.invoke_runner({"artifacts_dir": str(tmp_path), "criteria": []})
    sent = route.calls[0].request.read()
    assert b"/tmp" in sent and str(tmp_path).encode() not in sent  # local paths not leaked
    assert out["artifacts"][0]["file"] == "evil.png"  # path traversal stripped
    assert (tmp_path / "evil.png").read_bytes() == b"img"
