"""P05c/d — evidence-bound explanations of real verification results.

    agreement criterion → verification result → measurement / artifact → AI explanation

The model receives the deterministic results (as untrusted data), returns explanations
with explicit references, and `ground()` keeps only claims whose references exist and
whose reported outcome equals the stored outcome. Verification results are never written;
the analysis is stored separately. If the model is unavailable or nothing survives
grounding, the analysis falls back to a deterministic summary of the stored results.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from outcomeguard.ai.gateway import AIGateway
from outcomeguard.ai.prompts import (
    EVIDENCE_ANALYSIS_SYSTEM,
    EVIDENCE_ANALYSIS_VERSION,
    wrap_untrusted,
)
from outcomeguard.ai.schemas import EvidenceAnalysisOut
from outcomeguard.models.ai import AnalysisStatus, EvidenceAnalysis
from outcomeguard.models.audit import record_audit
from outcomeguard.models.verification import (
    TERMINAL_RUN_STATUSES,
    EvidenceArtifact,
    VerificationRun,
)

MAX_STR = 300
# Fields that are large or purely internal and add nothing to an explanation.
DROP_FIELDS = {"runs"}
# Summary phrases that would contradict a non-PASS verdict.
_PASS_CLAIM = re.compile(
    r"\b(all (criteria|checks|tests) (passed|pass)|verification (passed|succeeded)|"
    r"(ready|eligible|approved) for (payment|capture|release)|release (the )?payment|"
    r"capture (the )?payment)\b",
    re.IGNORECASE,
)


class AnalysisError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 409) -> None:
        self.code, self.message, self.http_status = code, message, http_status
        super().__init__(f"{code}: {message}")


def _clip(v: Any) -> Any:
    if isinstance(v, str):
        return v[:MAX_STR]
    if isinstance(v, list):
        return [_clip(x) for x in v[:10]]
    if isinstance(v, dict):
        return {k: _clip(x) for k, x in list(v.items())[:30] if k not in DROP_FIELDS}
    return v


def evidence_packet(session: Session, run: VerificationRun) -> dict[str, Any]:
    arts = session.scalars(select(EvidenceArtifact).where(EvidenceArtifact.run_id == run.id)).all()
    lh_runs = {}
    for r in run.results:
        if r.kind == "lighthouse_performance" and r.measured.get("runs"):
            lh_runs[r.id] = [
                {"score": x.get("score"), "elapsed_ms": x.get("elapsed_ms")}
                for x in r.measured["runs"]
            ]
    return {
        "run": {
            "run_id": run.id,
            "status": run.status.value,
            "verdict": run.verdict.value if run.verdict else None,
        },
        "results": [
            {
                "result_id": r.id,
                "criterion_key": r.criterion_key,
                "kind": r.kind,
                "blocking": r.blocking,
                "outcome": r.outcome.value,
                "reason_code": r.reason_code,
                "expected": _clip(r.expected),
                "measured": _clip(r.measured),
                **({"lighthouse_runs": lh_runs[r.id]} if r.id in lh_runs else {}),
                "artifact_ids": r.artifact_ids,
            }
            for r in run.results
        ],
        "artifacts": [
            {"artifact_id": a.id, "kind": a.kind.value, "label": a.label, "sha256": a.sha256}
            for a in arts
        ],
    }


def deterministic_summary(packet: dict[str, Any]) -> dict[str, Any]:
    """Fallback that states only stored facts — used when AI is unavailable or rejected."""
    res = packet["results"]
    return {
        "summary": f"Verdict {packet['run']['verdict']}: "
        + "; ".join(f"{r['criterion_key']} {r['outcome']} ({r['reason_code']})" for r in res),
        "source": "deterministic",
    }


def ground(packet: dict[str, Any], out: EvidenceAnalysisOut) -> dict[str, Any]:
    """Keep only claims that are bound to real results; never alter stored outcomes."""
    results = {r["result_id"]: r for r in packet["results"]}
    artifact_ids = {a["artifact_id"] for a in packet["artifacts"]}
    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    covered: set[str] = set()
    for item in out.criteria:
        reasons: list[str] = []
        actual = results.get(item.result_id)
        if actual is None:
            reasons.append("UNKNOWN_RESULT_ID")
        else:
            if actual["criterion_key"] != item.criterion_key:
                reasons.append("CRITERION_KEY_MISMATCH")
            if actual["outcome"] != item.outcome_reported:
                reasons.append("OUTCOME_CONTRADICTS_STORED_RESULT")
            if item.result_id in covered:
                reasons.append("DUPLICATE_EXPLANATION")
        if not item.evidence_refs and not item.insufficient_evidence:
            reasons.append("NO_EVIDENCE_REFERENCES")
        for ref in item.evidence_refs:
            target = results.get(ref.result_id)
            if target is None:
                reasons.append("REF_UNKNOWN_RESULT_ID")
                continue
            # A measurement ref must name a field that exists in the evidence packet for
            # that result: a top-level field (measured, expected, reason_code, ...) or a
            # key inside `measured`.
            if ref.measurement and ref.measurement.split(".")[0] not in (
                set(target["measured"]) | set(target)
            ):
                reasons.append(f"REF_UNKNOWN_MEASUREMENT:{ref.measurement[:40]}")
            if ref.artifact_id and ref.artifact_id not in artifact_ids:
                reasons.append("REF_UNKNOWN_ARTIFACT_ID")
        if reasons:
            rejected.append(
                {
                    "criterion_key": item.criterion_key,
                    "result_id": item.result_id,
                    "reasons": sorted(set(reasons)),
                }
            )
            continue
        covered.add(item.result_id)
        kept.append(
            {
                "criterion_key": item.criterion_key,
                "result_id": item.result_id,
                # Always the stored, deterministic outcome — the AI's copy is only checked.
                "outcome": actual["outcome"],
                "reason_code": actual["reason_code"],
                "blocking": actual["blocking"],
                "explanation": item.explanation,
                "evidence_refs": [r.model_dump(exclude_none=True) for r in item.evidence_refs],
                "remediation": item.remediation if actual["outcome"] != "pass" else None,
                "confidence": item.confidence,
                "insufficient_evidence": item.insufficient_evidence,
            }
        )
    summary = out.summary
    summary_rejected = None
    if packet["run"]["verdict"] != "pass" and _PASS_CLAIM.search(summary):
        summary_rejected = "SUMMARY_CONTRADICTS_VERDICT"
        summary = deterministic_summary(packet)["summary"]
    unexplained = [r["criterion_key"] for rid, r in results.items() if rid not in covered]
    return {
        "verdict": packet["run"]["verdict"],
        "summary": summary,
        "summary_rejected": summary_rejected,
        "explanations": kept,
        "rejected_claims": rejected,
        "unexplained_criteria": unexplained,
        "missing_evidence": out.missing_evidence,
        "limitations": out.limitations,
    }


@dataclass
class EvidenceIntelligence:
    session: Session
    gateway: AIGateway

    def analyze(self, run_id: str, *, actor: str) -> EvidenceAnalysis:
        run = self.session.get(VerificationRun, run_id)
        if run is None:
            raise AnalysisError("NOT_FOUND", "run not found", 404)
        if run.status not in TERMINAL_RUN_STATUSES:
            raise AnalysisError("RUN_NOT_FINISHED", f"run is {run.status.value}")
        packet = evidence_packet(self.session, run)
        verdict = packet["run"]["verdict"]
        if not packet["results"]:
            return self._store(
                run,
                None,
                AnalysisStatus.INSUFFICIENT_EVIDENCE,
                None,
                {
                    **deterministic_summary(packet),
                    "verdict": verdict,
                    "explanations": [],
                    "unexplained_criteria": [],
                },
                actor,
            )
        result = self.gateway.call(
            task="evidence_analysis",
            prompt_version=EVIDENCE_ANALYSIS_VERSION,
            system=EVIDENCE_ANALYSIS_SYSTEM,
            user=wrap_untrusted("evidence", packet),
            schema=EvidenceAnalysisOut,
            actor=actor,
        )
        if result.parsed is None:
            return self._store(
                run,
                result.call,
                AnalysisStatus.AI_UNAVAILABLE,
                None,
                {
                    **deterministic_summary(packet),
                    "verdict": verdict,
                    "ai_outcome": result.outcome,
                    "explanations": [],
                    "unexplained_criteria": [r["criterion_key"] for r in packet["results"]],
                },
                actor,
            )
        grounded = ground(packet, result.parsed)
        if not grounded["explanations"]:
            status = AnalysisStatus.REJECTED
            grounded["summary"] = deterministic_summary(packet)["summary"]
        elif grounded["rejected_claims"] or grounded["summary_rejected"]:
            status = AnalysisStatus.PARTIALLY_REJECTED
        elif all(e["insufficient_evidence"] for e in grounded["explanations"]):
            status = AnalysisStatus.INSUFFICIENT_EVIDENCE
        else:
            status = AnalysisStatus.GROUNDED
        return self._store(run, result.call, status, result.parsed.model_dump(), grounded, actor)

    def _store(self, run, call, status, ai_output, grounded, actor) -> EvidenceAnalysis:
        row = EvidenceAnalysis(
            run_id=run.id,
            ai_call_id=call.id if call else None,
            status=status,
            run_verdict=run.verdict.value if run.verdict else None,
            ai_output=ai_output,
            grounded=grounded,
            created_by=actor[:200],
        )
        self.session.add(row)
        self.session.flush()
        record_audit(
            self.session,
            actor=actor,
            action=f"ai.evidence_analysis.{status.value}",
            entity_type="evidence_analysis",
            entity_id=row.id,
            data={
                "run_id": run.id,
                "ai_call_id": row.ai_call_id,
                "rejected": len(grounded.get("rejected_claims", [])),
            },
        )
        self.session.commit()
        return row
