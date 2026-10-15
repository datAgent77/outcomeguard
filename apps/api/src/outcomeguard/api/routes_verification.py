"""Evidence submission, verification run read-out, artifact download, and the controlled
contact-form sink."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from outcomeguard.api.deps import AgreementServiceDep, PrincipalDep, SessionDep, SettingsDep
from outcomeguard.models import (
    Agreement,
    EvidenceArtifact,
    EvidenceSubmission,
    FormSinkReceipt,
    RunStatus,
    VerificationRun,
)
from outcomeguard.services.governance import evidence_snapshot_sha256
from outcomeguard.verification import sinktoken
from outcomeguard.verification.service import is_verified_pass, submit_evidence
from outcomeguard.verification.store import EvidenceIntegrityError, EvidenceStore

router = APIRouter(tags=["verification"])
sink_router = APIRouter(tags=["form-sink"])

MAX_SINK_BODY = 8 * 1024
MAX_RECEIPTS_PER_RUN = 5


class EvidenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delivery_url: str = Field(min_length=8, max_length=2048)
    repo_url: str | None = Field(default=None, max_length=2048)
    commit_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{7,64}$")
    notes: str | None = Field(default=None, max_length=5000)


class ResultOut(BaseModel):
    criterion_key: str
    kind: str
    blocking: bool
    outcome: str
    reason_code: str
    measured: dict[str, Any]
    expected: dict[str, Any]
    artifact_ids: list[str]
    duration_ms: int | None


class ArtifactOut(BaseModel):
    id: str
    kind: str
    label: str
    sha256: str
    size_bytes: int
    content_type: str


class RunOut(BaseModel):
    id: str
    status: str
    verdict: str | None
    verified_pass: bool
    # Bind manual-review decisions to this (P06).
    evidence_snapshot_sha256: str | None
    agreement_version_id: str
    agreement_content_hash: str
    delivery_url: str
    submission_hash: str
    tool_versions: dict[str, Any] | None
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    results: list[ResultOut]
    artifacts: list[ArtifactOut]


def _run_out(session, run: VerificationRun) -> RunOut:
    sub = session.get(EvidenceSubmission, run.submission_id)
    arts = session.scalars(select(EvidenceArtifact).where(EvidenceArtifact.run_id == run.id)).all()
    return RunOut(
        id=run.id,
        status=run.status.value,
        verdict=run.verdict.value if run.verdict else None,
        verified_pass=is_verified_pass(session, run.id),
        evidence_snapshot_sha256=evidence_snapshot_sha256(session, run)
        if run.status.value == "completed"
        else None,
        agreement_version_id=run.agreement_version_id,
        agreement_content_hash=run.agreement_content_hash,
        delivery_url=sub.delivery_url,
        submission_hash=sub.submission_hash,
        tool_versions=run.tool_versions,
        started_at=run.started_at,
        finished_at=run.finished_at,
        error=run.error,
        results=[
            ResultOut(
                criterion_key=r.criterion_key,
                kind=r.kind,
                blocking=r.blocking,
                outcome=r.outcome.value,
                reason_code=r.reason_code,
                measured=r.measured,
                expected=r.expected,
                artifact_ids=r.artifact_ids,
                duration_ms=r.duration_ms,
            )
            for r in run.results
        ],
        artifacts=[
            ArtifactOut(
                id=a.id,
                kind=a.kind.value,
                label=a.label,
                sha256=a.sha256,
                size_bytes=a.size_bytes,
                content_type=a.content_type,
            )
            for a in arts
        ],
    )


def _readable_run(session, svc, principal, run_id: str) -> VerificationRun:
    run = session.get(VerificationRun, run_id)
    if run is None:
        raise HTTPException(404, "not found")
    from outcomeguard.models import AgreementVersion

    version = session.get(AgreementVersion, run.agreement_version_id)
    svc.get(principal, version.agreement_id)  # 404 unless buyer/merchant may read
    return run


@router.post("/agreements/{agreement_id}/evidence", response_model=RunOut, status_code=201)
def submit(
    agreement_id: str,
    body: EvidenceIn,
    principal: PrincipalDep,
    svc: AgreementServiceDep,
    session: SessionDep,
) -> RunOut:
    agreement: Agreement = svc.get(principal, agreement_id)
    run = submit_evidence(
        session,
        principal,
        agreement,
        delivery_url=body.delivery_url,
        repo_url=body.repo_url,
        commit_sha=body.commit_sha,
        notes=body.notes,
    )
    return _run_out(session, run)


@router.get("/agreements/{agreement_id}/verification-runs", response_model=list[RunOut])
def list_runs(
    agreement_id: str, principal: PrincipalDep, svc: AgreementServiceDep, session: SessionDep
) -> list[RunOut]:
    agreement = svc.get(principal, agreement_id)
    ids = [v.id for v in agreement.versions]
    runs = session.scalars(
        select(VerificationRun)
        .where(VerificationRun.agreement_version_id.in_(ids))
        .order_by(VerificationRun.created_at.desc())
    ).all()
    return [_run_out(session, r) for r in runs]


@router.get("/verification/runs/{run_id}", response_model=RunOut)
def get_run(
    run_id: str, principal: PrincipalDep, svc: AgreementServiceDep, session: SessionDep
) -> RunOut:
    return _run_out(session, _readable_run(session, svc, principal, run_id))


@router.get("/verification/artifacts/{artifact_id}")
def get_artifact(
    artifact_id: str,
    principal: PrincipalDep,
    svc: AgreementServiceDep,
    session: SessionDep,
    settings: SettingsDep,
) -> Response:
    art = session.get(EvidenceArtifact, artifact_id)
    if art is None:
        raise HTTPException(404, "not found")
    _readable_run(session, svc, principal, art.run_id)
    try:
        data = EvidenceStore(settings.evidence_dir).get(art.sha256)
    except EvidenceIntegrityError as exc:
        raise HTTPException(409, f"evidence integrity check failed: {exc}") from exc
    return Response(
        data,
        media_type=art.content_type,
        headers={
            "X-Content-SHA256": art.sha256,
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="{art.label}"',
        },
    )


# ------------------------------------------------------------- form test sink

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "600",
}


@sink_router.options("/sinks/{ref}")
def sink_preflight(ref: str) -> Response:
    return Response(status_code=204, headers=CORS)


@sink_router.post("/sinks/{ref}")
async def sink(ref: str, request: Request, session: SessionDep, settings: SettingsDep) -> Response:
    """Receives contact-form test submissions — only from an active verification run.

    Stores field names and a hash of the values; never the values. Requests without a valid
    run token (attached by the verifier's browser, invisible to the page) are rejected and
    leave no trace, so the sink is useless as a general-purpose public endpoint."""
    refs = {r.strip() for r in settings.verifier_form_sinks.split(",") if r.strip()}
    secret = settings.sink_secret.get_secret_value() if settings.sink_secret else None
    token = request.headers.get("x-outcomeguard-sink-token", "")
    run_id = sinktoken.verify(secret, token, ref) if (secret and ref in refs) else None
    if run_id is None:
        return Response(
            '{"accepted":false}', status_code=403, media_type="application/json", headers=CORS
        )
    run = session.get(VerificationRun, run_id)
    if run is None or run.status is not RunStatus.RUNNING:
        return Response(
            '{"accepted":false}', status_code=403, media_type="application/json", headers=CORS
        )
    raw = await request.body()
    if len(raw) > MAX_SINK_BODY:
        return Response(
            '{"accepted":false}', status_code=413, media_type="application/json", headers=CORS
        )
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    try:
        if ctype == "application/json":
            data = json.loads(raw or b"{}")
            pairs = [(str(k), str(v)) for k, v in data.items()] if isinstance(data, dict) else []
        elif ctype == "application/x-www-form-urlencoded":
            pairs = parse_qsl(raw.decode("utf-8"), keep_blank_values=True)
        else:
            return Response(
                '{"accepted":false}', status_code=415, media_type="application/json", headers=CORS
            )
    except (ValueError, UnicodeDecodeError):
        return Response(
            '{"accepted":false}', status_code=400, media_type="application/json", headers=CORS
        )
    filled = [(k, v) for k, v in pairs if v.strip()]
    if not filled:
        return Response(
            '{"accepted":false,"reason":"empty"}',
            status_code=422,
            media_type="application/json",
            headers=CORS,
        )
    count = (
        session.scalar(
            select(func.count())
            .select_from(FormSinkReceipt)
            .where(FormSinkReceipt.run_id == run.id)
        )
        or 0
    )
    if count >= MAX_RECEIPTS_PER_RUN:
        return Response(
            '{"accepted":false}', status_code=429, media_type="application/json", headers=CORS
        )
    session.add(
        FormSinkReceipt(
            run_id=run.id,
            sink_ref=ref,
            field_names=sorted({k[:60] for k, _ in pairs})[:20],
            values_sha256=hashlib.sha256(json.dumps(sorted(pairs)).encode()).hexdigest(),
            content_length=len(raw),
            origin=(request.headers.get("origin") or "")[:300] or None,
        )
    )
    session.commit()
    return Response(
        '{"accepted":true}', status_code=201, media_type="application/json", headers=CORS
    )
