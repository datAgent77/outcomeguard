"""End-to-end, nothing mocked except the network position: real API (uvicorn, hosting the
form sink), real fixture pages, real verification worker invoking the real runner
subprocess (Chromium + Lighthouse through the egress proxy), real PostgreSQL.

    broken landing page → FAIL   (contact form never reaches the sink)
    fixed landing page  → PASS   (all blocking criteria pass)
    exfil landing page  → FAIL   (FORM_POSTS_TO_UNAPPROVED_DESTINATION; collector got 0 bytes)
"""

import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from sqlalchemy import select

from outcomeguard.domain.verdict import RunVerdict
from outcomeguard.models import EvidenceArtifact, RunStatus, VerificationRun
from outcomeguard.verification.service import (
    Orchestrator,
    claim_job,
    is_verified_pass,
    submit_evidence,
)
from outcomeguard.verification.settings import VerifierSettings
from outcomeguard.verification.store import EvidenceStore
from tests.conftest import TEST_DB
from tests.vhelpers import Canary

pytestmark = pytest.mark.browser

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "fixtures"))
import serve as fixture_server  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_api(make_session):
    from outcomeguard.main import create_app

    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning")
    )
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield port
    server.should_exit = True
    t.join(5)


@pytest.fixture
def stack(live_api, tmp_path):
    collector = Canary()
    fx_port = free_port()
    fx = fixture_server.start(
        fx_port, f"http://127.0.0.1:{live_api}/sinks/default", f"{collector.base}/collect"
    )
    settings = VerifierSettings(
        database_url=TEST_DB,
        sink_secret="test-sink-secret",
        sink_base_url=f"http://127.0.0.1:{live_api}",
        verifier_dev_allow=f"127.0.0.1:{fx_port},127.0.0.1:{live_api},127.0.0.1:{collector.port}",
        evidence_dir=str(tmp_path / "evidence"),
    )
    yield settings, fx_port, collector
    fx.shutdown()
    collector.close()


def verify(session, who, agreement, settings, url) -> VerificationRun:
    run = submit_evidence(session, who("maya"), agreement, delivery_url=url)
    job = claim_job(session, "e2e-worker", 600)
    assert job is not None and job.run_id == run.id
    t0 = time.monotonic()
    out = Orchestrator(session, settings, "e2e-worker").process(job)
    print(
        f"\n[{url}] {out.status.value}/{out.verdict.value if out.verdict else None} "
        f"in {time.monotonic() - t0:.1f}s"
    )
    session.expire_all()
    return session.get(VerificationRun, run.id)


def outcomes(run) -> dict[str, tuple[str, str]]:
    return {r.criterion_key: (r.outcome.value, r.reason_code) for r in run.results}


def test_broken_fixed_and_exfil_pages_end_to_end(session, who, locked_agreement, stack):
    settings, fx_port, collector = stack
    base = f"http://127.0.0.1:{fx_port}"

    broken = verify(session, who, locked_agreement, settings, f"{base}/landing-page-broken/")
    o = outcomes(broken)
    print(o)
    assert broken.status is RunStatus.COMPLETED and broken.verdict is RunVerdict.FAIL
    assert o["contact_form_works"] == ("fail", "NO_SUBMISSION_TO_SINK")
    assert o["site_available"][0] == "pass" and o["mobile_layout"][0] == "pass"
    assert not is_verified_pass(session, broken.id)

    fixed = verify(session, who, locked_agreement, settings, f"{base}/landing-page-fixed/")
    o = outcomes(fixed)
    print(o)
    assert fixed.status is RunStatus.COMPLETED and fixed.verdict is RunVerdict.PASS, o
    assert all(v[0] == "pass" for v in o.values())
    assert is_verified_pass(session, fixed.id)
    lh = next(r for r in fixed.results if r.kind == "lighthouse_performance")
    assert lh.measured["scores"] and all(isinstance(s, int) for s in lh.measured["scores"])
    print("lighthouse:", lh.measured["scores"], [r.get("elapsed_ms") for r in lh.measured["runs"]])
    form = next(r for r in fixed.results if r.kind == "contact_form")
    assert form.measured["sink_receipts_in_db"] == 1

    exfil = verify(session, who, locked_agreement, settings, f"{base}/landing-page-exfil/")
    o = outcomes(exfil)
    print(o)
    assert exfil.verdict is RunVerdict.FAIL
    assert o["contact_form_works"] == ("fail", "FORM_POSTS_TO_UNAPPROVED_DESTINATION")
    assert collector.hits == [], "test data reached an unapproved destination"

    # Evidence artifacts persisted and hash-verified.
    store = EvidenceStore(settings.evidence_dir)
    for run in (broken, fixed, exfil):
        arts = session.scalars(
            select(EvidenceArtifact).where(EvidenceArtifact.run_id == run.id)
        ).all()
        kinds = {a.kind.value for a in arts}
        assert {"screenshot", "http_trace", "lighthouse_json", "network_log"} <= kinds, kinds
        for a in arts:
            assert len(store.get(a.sha256)) == a.size_bytes
    assert fixed.tool_versions["chromium"] and fixed.tool_versions["lighthouse"]
