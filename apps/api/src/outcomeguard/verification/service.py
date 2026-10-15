"""Verification orchestration: evidence submission, job queue, runner invocation and
deterministic persistence of results.

The orchestrator holds database access but no PayPal credentials. The runner (a separate
process with a scrubbed environment) measures; this module decides — via the pure
functions in domain/verdict.py — and writes results, artifacts and the verdict in one
transaction while the run is `running`. After that the database refuses changes.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from outcomeguard.auth import Forbidden, Principal
from outcomeguard.domain.verdict import CriterionOutcome, RunVerdict, evaluate, run_verdict
from outcomeguard.models import (
    AcceptanceCriterion,
    Agreement,
    AgreementVersion,
    MerchantAccount,
    Role,
    VersionStatus,
)
from outcomeguard.models.audit import record_audit
from outcomeguard.models.base import utcnow
from outcomeguard.models.verification import (
    ArtifactKind,
    EvidenceArtifact,
    EvidenceSubmission,
    FormSinkReceipt,
    JobState,
    RunStatus,
    VerificationJob,
    VerificationResult,
    VerificationRun,
)
from outcomeguard.verification import sinktoken
from outcomeguard.verification.netpolicy import PolicyViolation, validate_url
from outcomeguard.verification.settings import VerifierSettings
from outcomeguard.verification.store import EvidenceStore

SUBMIT_ROLES = (Role.PROVIDER, Role.MERCHANT_ADMIN)
RUNNER_ENV_KEEP = (
    "PATH",
    "HOME",
    "LANG",
    "TMPDIR",
    "PLAYWRIGHT_BROWSERS_PATH",
    "APP_ENV",
    "VERIFIER_DEV_ALLOW",
    "VERIFIER_CHROMIUM_SANDBOX",
)


class VerificationError(Exception):
    def __init__(self, code: str, message: str, *, http_status: int = 409) -> None:
        self.code = code
        self.message = message
        self.http_status = http_status
        super().__init__(f"{code}: {message}")


# ------------------------------------------------------------------ submission


def submit_evidence(
    session: Session,
    principal: Principal,
    agreement: Agreement,
    *,
    delivery_url: str,
    repo_url: str | None = None,
    commit_sha: str | None = None,
    notes: str | None = None,
) -> VerificationRun:
    merchant = session.get(MerchantAccount, agreement.merchant_account_id)
    if merchant is None or not principal.has(merchant.organization_id, *SUBMIT_ROLES):
        raise Forbidden("submitting evidence requires the provider role of the contracted merchant")
    if agreement.current_locked_version_id is None:
        raise VerificationError("AGREEMENT_NOT_LOCKED", "no locked agreement version to verify")
    version = session.get(AgreementVersion, agreement.current_locked_version_id)
    assert version is not None and version.status is VersionStatus.LOCKED
    try:
        validate_url(delivery_url)
    except PolicyViolation as exc:
        raise VerificationError(exc.code, "delivery URL rejected", http_status=422) from exc

    canonical = json.dumps(
        {
            "agreement_version_id": version.id,
            "delivery_url": delivery_url,
            "repo_url": repo_url,
            "commit_sha": commit_sha,
            "notes": notes,
            "by": principal.user_id,
        },
        sort_keys=True,
    )
    sub = EvidenceSubmission(
        agreement_version_id=version.id,
        submitted_by_user_id=principal.user_id,
        delivery_url=delivery_url,
        repo_url=repo_url,
        commit_sha=commit_sha,
        notes=notes,
        submission_hash=hashlib.sha256(canonical.encode()).hexdigest(),
    )
    session.add(sub)
    session.flush()
    run = VerificationRun(
        submission_id=sub.id,
        agreement_version_id=version.id,
        agreement_content_hash=version.content_hash or "",
        status=RunStatus.QUEUED,
    )
    session.add(run)
    session.flush()
    session.add(VerificationJob(run_id=run.id, state=JobState.QUEUED))
    record_audit(
        session,
        actor=principal.label,
        action="evidence.submitted",
        entity_type="evidence_submission",
        entity_id=sub.id,
        data={"run_id": run.id, "delivery_url": delivery_url, "agreement_version_id": version.id},
    )
    session.commit()
    return run


# ----------------------------------------------------------------------- queue


def claim_job(session: Session, worker_id: str, lease_seconds: int) -> VerificationJob | None:
    """Claim one runnable job. SKIP LOCKED makes concurrent workers take different jobs;
    an expired lease (crashed worker) makes the job claimable again."""
    now = utcnow()
    job = session.scalar(
        select(VerificationJob)
        .where(
            or_(
                (VerificationJob.state == JobState.QUEUED) & (VerificationJob.available_at <= now),
                (VerificationJob.state == JobState.LEASED) & (VerificationJob.lease_until < now),
            )
        )
        .order_by(VerificationJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        session.rollback()
        return None
    run = session.get(VerificationRun, job.run_id, with_for_update=True, populate_existing=True)
    assert run is not None
    if job.attempts >= job.max_attempts:
        _give_up(session, job, run, "max attempts exhausted (lease expired)")
        session.commit()
        return None
    job.state = JobState.LEASED
    job.attempts += 1
    job.lease_owner = worker_id
    job.lease_until = now + timedelta(seconds=lease_seconds)
    if run.status is RunStatus.QUEUED:
        run.status = RunStatus.RUNNING
        run.started_at = now
    run.worker_id = worker_id
    session.commit()
    return job


def _give_up(session: Session, job: VerificationJob, run: VerificationRun, reason: str) -> None:
    job.state = JobState.FAILED
    job.last_error = reason[:2000]
    if run.status not in (RunStatus.COMPLETED, RunStatus.ERRORED):
        run.status = RunStatus.ERRORED
        run.verdict = RunVerdict.INCONCLUSIVE  # never PASS
        run.error = reason[:2000]
        run.finished_at = utcnow()
        record_audit(
            session,
            actor=f"verifier:{job.lease_owner}",
            action="verification.errored",
            entity_type="verification_run",
            entity_id=run.id,
            data={"reason": reason[:300]},
        )


# --------------------------------------------------------------------- execute


@dataclass
class Orchestrator:
    session: Session
    settings: VerifierSettings
    worker_id: str

    @property
    def store(self) -> EvidenceStore:
        return EvidenceStore(self.settings.evidence_dir)

    def criteria(self, run: VerificationRun) -> list[AcceptanceCriterion]:
        return list(
            self.session.scalars(
                select(AcceptanceCriterion)
                .where(AcceptanceCriterion.agreement_version_id == run.agreement_version_id)
                .order_by(AcceptanceCriterion.position)
            )
        )

    def build_job_spec(self, run: VerificationRun, workdir: Path) -> dict[str, Any]:
        sub = self.session.get(EvidenceSubmission, run.submission_id)
        assert sub is not None
        sinks = {}
        secret = self.settings.sink_secret.get_secret_value() if self.settings.sink_secret else None
        for c in self.criteria(run):
            ref = c.spec.get("test_endpoint_ref") if c.kind.value == "contact_form" else None
            if ref and secret and ref in self.settings.sink_refs:
                sinks[ref] = {
                    "url": f"{self.settings.sink_base_url.rstrip('/')}/sinks/{ref}",
                    "token": sinktoken.issue(secret, run.id, ref),
                }
        return {
            "run_id": run.id,
            "delivery_url": sub.delivery_url,
            "artifacts_dir": str(workdir),
            "criteria": [
                {"criterion_id": c.id, "key": c.key, "kind": c.kind.value, "spec": c.spec}
                for c in self.criteria(run)
                if c.kind.value != "manual_review"
            ],
            "sinks": sinks,
            "lighthouse": {
                "bin": self.settings.lighthouse_bin,
                "band": self.settings.lighthouse_band,
            },
            "proxy_url": self.settings.verifier_proxy_url,
        }

    def invoke_runner(self, spec: dict[str, Any]) -> dict[str, Any]:
        if self.settings.verifier_runner_url:
            return self._invoke_remote_runner(spec)
        env = {k: v for k, v in os.environ.items() if k in RUNNER_ENV_KEEP}
        if self.settings.verifier_dev_allow:
            env["VERIFIER_DEV_ALLOW"] = self.settings.verifier_dev_allow
        env["APP_ENV"] = self.settings.app_env
        proc = subprocess.run(
            [sys.executable, "-m", "outcomeguard.verification.runner"],
            input=json.dumps(spec),
            capture_output=True,
            text=True,
            env=env,
            timeout=self.settings.runner_timeout_seconds,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"runner exited {proc.returncode}: {proc.stderr[-500:]}")
        return json.loads(proc.stdout)

    def _invoke_remote_runner(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Container mode: the runner lives in a container whose only network is the
        sandbox (proxy + this worker). Artifacts come back inline and are written into the
        local run directory so persistence is identical to subprocess mode."""
        import base64

        import httpx

        workdir = Path(spec["artifacts_dir"])
        resp = httpx.post(
            f"{self.settings.verifier_runner_url.rstrip('/')}/run",
            json={**spec, "artifacts_dir": "/tmp"},  # noqa: S108 — remote side
            timeout=self.settings.runner_timeout_seconds,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"runner server {resp.status_code}: {resp.text[:300]}")
        out = resp.json()
        for item in out.get("artifacts", []):
            name = Path(item["file"]).name
            (workdir / name).write_bytes(base64.b64decode(item.pop("data_b64", "")))
            item["file"] = name
        return out

    def process(self, job: VerificationJob) -> VerificationRun:
        run = self.session.get(VerificationRun, job.run_id)
        assert run is not None
        with tempfile.TemporaryDirectory(prefix="og-run-") as tmp:
            try:
                output = self.invoke_runner(self.build_job_spec(run, Path(tmp)))
            except Exception as exc:  # noqa: BLE001 — runner failure → retry or give up
                return self._attempt_failed(job.id, f"{type(exc).__name__}: {exc}")
            return self._persist(job.id, output, Path(tmp))

    def _attempt_failed(self, job_id: str, reason: str) -> VerificationRun:
        self.session.rollback()
        job = self.session.get(
            VerificationJob, job_id, with_for_update=True, populate_existing=True
        )
        assert job is not None
        run = self.session.get(
            VerificationRun, job.run_id, with_for_update=True, populate_existing=True
        )
        assert run is not None
        if job.attempts >= job.max_attempts:
            _give_up(self.session, job, run, reason)
        else:
            job.state = JobState.QUEUED
            job.lease_owner = None
            job.lease_until = None
            job.last_error = reason[:2000]
            job.available_at = utcnow() + timedelta(seconds=30 * job.attempts)
        self.session.commit()
        return run

    def _persist(self, job_id: str, output: dict[str, Any], workdir: Path) -> VerificationRun:
        s = self.session
        job = s.get(VerificationJob, job_id, with_for_update=True, populate_existing=True)
        assert job is not None
        if job.state is not JobState.LEASED or job.lease_owner != self.worker_id:
            s.rollback()
            raise VerificationError("LEASE_LOST", "another worker owns this job now")
        run = s.get(VerificationRun, job.run_id, with_for_update=True, populate_existing=True)
        assert run is not None and run.status is RunStatus.RUNNING

        # Artifacts: content-addressed store + rows (hash recorded at write time).
        art_ids_by_criterion: dict[str | None, list[str]] = {}
        for item in output.get("artifacts", []):
            src = (workdir / item["file"]).resolve()
            if workdir.resolve() not in src.parents:
                continue  # never follow paths outside the run directory
            digest, size = self.store.put(src.read_bytes())
            art = EvidenceArtifact(
                run_id=run.id,
                kind=ArtifactKind(item["kind"]),
                label=item["label"][:120],
                sha256=digest,
                size_bytes=size,
                content_type=item["content_type"][:80],
            )
            s.add(art)
            s.flush()
            art_ids_by_criterion.setdefault(item.get("criterion_id"), []).append(art.id)

        receipts = (
            s.scalar(
                select(func.count())
                .select_from(FormSinkReceipt)
                .where(FormSinkReceipt.run_id == run.id)
            )
            or 0
        )
        measurements = output.get("measurements", {})
        timings = output.get("timings_ms", {})
        outcomes: list[tuple[bool, CriterionOutcome]] = []
        for c in self.criteria(run):
            ev = evaluate(c.kind.value, c.spec, measurements.get(c.id), sink_receipts=receipts)
            measured = dict(measurements.get(c.id) or {})
            if c.kind.value == "contact_form":
                measured["sink_receipts_in_db"] = receipts
            s.add(
                VerificationResult(
                    run_id=run.id,
                    criterion_id=c.id,
                    position=c.position,
                    criterion_key=c.key,
                    kind=c.kind.value,
                    blocking=c.blocking,
                    outcome=ev.outcome,
                    reason_code=ev.reason_code,
                    measured=json.loads(json.dumps(measured, default=str)),
                    expected=c.spec,
                    artifact_ids=art_ids_by_criterion.get(c.id, []),
                    duration_ms=timings.get(c.id),
                )
            )
            outcomes.append((c.blocking, ev.outcome))
        s.flush()
        run.verdict = run_verdict(outcomes)
        run.tool_versions = output.get("tool_versions")
        run.config = {
            "lighthouse_band": self.settings.lighthouse_band,
            "egress_blocked": output.get("egress_blocked", [])[:50],
            "network_log_artifacts": art_ids_by_criterion.get(None, []),
        }
        run.status = RunStatus.COMPLETED
        run.finished_at = utcnow()
        job.state = JobState.DONE
        job.lease_until = None
        record_audit(
            s,
            actor=f"verifier:{self.worker_id}",
            action="verification.completed",
            entity_type="verification_run",
            entity_id=run.id,
            data={
                "verdict": run.verdict.value,
                "outcomes": {
                    c: o.value
                    for c, (_, o) in zip(
                        [x.key for x in self.criteria(run)], outcomes, strict=False
                    )
                },
            },
        )
        s.commit()
        return run


def is_verified_pass(session: Session, run_id: str) -> bool:
    """The only predicate later phases may use to treat a run as passing."""
    run = session.get(VerificationRun, run_id)
    if run is None or run.status is not RunStatus.COMPLETED or run.verdict is not RunVerdict.PASS:
        return False
    criteria = session.scalars(
        select(AcceptanceCriterion).where(
            AcceptanceCriterion.agreement_version_id == run.agreement_version_id
        )
    ).all()
    results = {r.criterion_id: r for r in run.results}
    return all(
        c.id in results and results[c.id].outcome is CriterionOutcome.PASS
        for c in criteria
        if c.blocking
    )


def new_worker_id() -> str:
    return f"{os.uname().nodename[:40]}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
