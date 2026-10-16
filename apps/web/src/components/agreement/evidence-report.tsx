"use client";

import { useState } from "react";
import { api, ApiError } from "@/lib/client/api";
import { useApi } from "@/lib/client/hooks";
import type { Analysis, Artifact, Criterion, Run, RunResult, Version } from "@/lib/types";
import { ERROR_HELP, KIND_LABEL, OUTCOME, VERDICT } from "@/lib/status";
import { dateTime, humanize } from "@/lib/format";
import { Badge, Button, Card, cx, Hash, Notice } from "../ui";
import { ArtifactButton, ArtifactViewer } from "./artifacts";
import { ManualReviewPanel } from "./manual-review";
import { summarize, TOOL } from "./result-summary";

export function EvidenceReport({
  run,
  version,
  buyerOrgId,
  isLatestCompleted,
  onReviewed,
}: {
  run: Run;
  version: Version | undefined;
  buyerOrgId: string;
  isLatestCompleted: boolean;
  onReviewed: () => void;
}) {
  const analyses = useApi<Analysis[]>(`/verification/runs/${run.id}/analyses`);
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [aiBusy, setAiBusy] = useState(false);
  const [aiError, setAiError] = useState<ApiError | null>(null);
  const analysis = analyses.data?.[0];
  const verdict = run.verdict ? VERDICT[run.verdict] : null;
  const counts = run.results.reduce<Record<string, number>>((m, r) => ({ ...m, [r.outcome]: (m[r.outcome] ?? 0) + 1 }), {});
  const criteria = new Map<string, Criterion>((version?.criteria ?? []).map((c) => [c.id, c]));
  const arts = new Map(run.artifacts.map((a) => [a.id, a]));

  async function explain() {
    setAiBusy(true);
    setAiError(null);
    try {
      await api<Analysis>(`/verification/runs/${run.id}/analysis`, { method: "POST" });
      analyses.reload();
    } catch (e) {
      setAiError(e as ApiError);
    } finally {
      setAiBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-4 border-b border-ink-100 px-4 py-4 sm:px-5">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-wider text-ink-500">Verification Report</p>
            <h2 className="mt-1 text-lg font-semibold">
              {verdict ? (
                <span className={cx(verdict.tone === "pass" && "text-pass-700", verdict.tone === "fail" && "text-fail-700", verdict.tone === "review" && "text-review-800")}>
                  {verdict.label}
                </span>
              ) : (
                humanize(run.status)
              )}
            </h2>
            {verdict && <p className="mt-0.5 max-w-xl text-sm text-ink-600">{verdict.help}</p>}
          </div>
          <div className="flex flex-wrap gap-2 text-sm">
            <Count n={counts.pass ?? 0} label="passed" tone="pass" />
            <Count n={(counts.fail ?? 0) + (counts.error ?? 0)} label="failed / inconclusive" tone="fail" />
            <Count n={counts.manual_review_required ?? 0} label="pending human review" tone="review" />
          </div>
        </div>
        <dl className="grid gap-x-6 gap-y-3 px-4 py-4 text-sm sm:grid-cols-2 sm:px-5 lg:grid-cols-3">
          <Meta k="Verified at" v={dateTime(run.finished_at)} />
          <Meta k="Agreement version" v={version ? `v${version.version_number}` : "—"} />
          <Meta k="Agreement content hash" v={<Hash value={run.agreement_content_hash} n={12} />} />
          <Meta k="Verification run" v={<Hash value={run.id} n={12} />} />
          <Meta k="Evidence snapshot" v={<Hash value={run.evidence_snapshot_sha256} n={12} label="Evidence snapshot hash" />} />
          <Meta k="Delivery URL" v={<span className="break-all font-mono text-xs">{run.delivery_url}</span>} />
        </dl>
        {!isLatestCompleted && (
          <div className="px-4 pb-4 sm:px-5">
            <Notice tone="review">This is not the latest completed run. Payment policy uses only the latest one.</Notice>
          </div>
        )}
      </Card>

      <div className="flex flex-wrap gap-2 text-xs" aria-label="Who decides what">
        <span className="rounded-full border border-ink-200 bg-white px-3 py-1"><strong>Measured</strong> — deterministic tools decide PASS / FAIL</span>
        <span className="rounded-full border border-brand-100 bg-brand-50 px-3 py-1 text-brand-700"><strong>AI interpretation</strong> — explains, never decides</span>
        <span className="rounded-full border border-review-100 bg-review-50 px-3 py-1 text-review-800"><strong>Human decision</strong> — only for what no tool can measure</span>
      </div>

      <Card>
        <div className="flex flex-wrap items-start justify-between gap-3 px-4 py-4 sm:px-5">
          <div className="min-w-0">
            <h3 className="text-sm font-semibold">AI explanation</h3>
            <p className="mt-0.5 text-xs text-ink-500">An interpretation of the deterministic results above — not the verdict. Every claim is checked against the stored measurements.</p>
          </div>
          {!analysis && (
            <Button size="sm" variant="secondary" onClick={explain} busy={aiBusy}>
              Explain with AI
            </Button>
          )}
        </div>
        <div className="px-4 pb-4 sm:px-5">
          {aiError && <Notice tone="fail" title={aiError.code}>{ERROR_HELP[aiError.code] ?? aiError.message} The deterministic report above is unaffected.</Notice>}
          {analyses.error && <Notice tone="review">AI analysis unavailable: {analyses.error.message}</Notice>}
          {analysis ? (
            <div className="space-y-2 text-sm">
              <Badge tone={analysis.status === "grounded" ? "info" : "review"}>AI interpretation · {humanize(analysis.status)}</Badge>
              {analysis.grounded.summary && <p className="text-ink-800">{analysis.grounded.summary}</p>}
              {!!analysis.grounded.limitations?.length && (
                <details>
                  <summary className="cursor-pointer text-ink-600">Limitations ({analysis.grounded.limitations.length})</summary>
                  <ul className="mt-2 list-disc space-y-1 pl-5 text-ink-600">
                    {analysis.grounded.limitations.map((l, i) => (
                      <li key={i}>{l}</li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          ) : (
            !analyses.loading && <p className="text-sm text-ink-500">No AI explanation yet. The verdict does not depend on it.</p>
          )}
        </div>
      </Card>

      <ol className="space-y-4" aria-label="Criteria results">
        {run.results.map((r) => (
          <ResultCard
            key={r.criterion_id}
            r={r}
            criterion={criteria.get(r.criterion_id)}
            artifacts={r.artifact_ids.map((id) => arts.get(id)).filter((a): a is Artifact => !!a)}
            explanation={analysis?.grounded.explanations?.find((e) => e.criterion_key === r.criterion_key)}
            onOpen={setArtifact}
            run={run}
            buyerOrgId={buyerOrgId}
            isLatestCompleted={isLatestCompleted}
            onReviewed={onReviewed}
          />
        ))}
      </ol>

      {(run.form_receipts.length > 0 || run.artifacts.some((a) => a.kind === "network_log")) && (
        <Card>
          <div className="px-4 py-4 sm:px-5">
            <h3 className="text-sm font-semibold">Supporting records</h3>
            {run.form_receipts.length > 0 && (
              <div className="mt-3">
                <p className="text-xs font-semibold uppercase tracking-wider text-ink-500">Form-sink receipts</p>
                <ul className="mt-2 space-y-2 text-sm">
                  {run.form_receipts.map((f) => (
                    <li key={f.id} className="rounded-lg border border-ink-200 px-3 py-2">
                      Received {dateTime(f.received_at)} at test endpoint <code className="font-mono text-xs">{f.sink_ref}</code> · fields{" "}
                      {f.field_names.join(", ")} · {f.content_length} bytes · values hash <Hash value={f.values_sha256} />
                      <span className="block text-xs text-ink-500">Only field names and a hash are stored — never the submitted values.</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <div className="mt-3 flex flex-wrap gap-2">
              {run.artifacts
                .filter((a) => a.kind === "network_log")
                .map((a) => (
                  <ArtifactButton key={a.id} artifact={a} onOpen={setArtifact} />
                ))}
            </div>
          </div>
        </Card>
      )}
      {run.tool_versions && (
        <p className="text-xs text-ink-500">
          Tools: {Object.entries(run.tool_versions).map(([k, v]) => `${k} ${typeof v === "string" ? v : JSON.stringify(v)}`).join(" · ")}
        </p>
      )}
      <ArtifactViewer artifact={artifact} onClose={() => setArtifact(null)} />
    </div>
  );
}

function Count({ n, label, tone }: { n: number; label: string; tone: "pass" | "fail" | "review" }) {
  const c = { pass: "text-pass-700", fail: "text-fail-700", review: "text-review-800" }[tone];
  return (
    <div className="rounded-lg border border-ink-200 px-3 py-1.5">
      <span className={cx("text-base font-semibold tabular-nums", n ? c : "text-ink-500")}>{n}</span> <span className="text-xs text-ink-500">{label}</span>
    </div>
  );
}

function Meta({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-ink-500">{k}</dt>
      <dd className="mt-0.5 min-w-0 font-medium">{v}</dd>
    </div>
  );
}

function Values({ obj }: { obj: Record<string, unknown> }) {
  const entries = Object.entries(obj).filter(([k]) => k !== "kind");
  if (!entries.length) return <span className="text-ink-500">No measurement (not measurable by a tool)</span>;
  return (
    <dl className="space-y-1">
      {entries.map(([k, v]) => (
        <div key={k} className="grid grid-cols-[minmax(0,10rem)_1fr] gap-2">
          <dt className="truncate text-ink-500" title={k}>
            {humanize(k)}
          </dt>
          <dd className="min-w-0 break-words font-mono text-xs leading-5 text-ink-800">{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

function ResultCard({
  r,
  criterion,
  artifacts,
  explanation,
  onOpen,
  run,
  buyerOrgId,
  isLatestCompleted,
  onReviewed,
}: {
  r: RunResult;
  criterion: Criterion | undefined;
  artifacts: Artifact[];
  explanation: NonNullable<Analysis["grounded"]["explanations"]>[number] | undefined;
  onOpen: (a: Artifact) => void;
  run: Run;
  buyerOrgId: string;
  isLatestCompleted: boolean;
  onReviewed: () => void;
}) {
  const o = OUTCOME[r.outcome] ?? { label: r.outcome, tone: "neutral" as const };
  const border = { pass: "border-l-pass-600", fail: "border-l-fail-600", review: "border-l-review-700", neutral: "border-l-ink-300", info: "border-l-brand-500" }[o.tone];
  return (
    <li className={cx("rounded-xl border border-l-4 border-ink-200 bg-white", border)} data-testid={`result-${r.criterion_key}`}>
      <div className="flex flex-wrap items-start justify-between gap-3 px-4 py-3 sm:px-5">
        <div className="min-w-0">
          <h4 className="font-semibold text-ink-900">{criterion?.title ?? r.criterion_key}</h4>
          <p className="mt-0.5 text-xs text-ink-500">
            {KIND_LABEL[r.kind]} · {r.blocking ? "Blocking" : "Non-blocking"} · reason <code className="font-mono">{r.reason_code}</code>
            {r.duration_ms != null && ` · ${(r.duration_ms / 1000).toFixed(1)} s`}
          </p>
        </div>
        <Badge tone={o.tone}>{o.label}</Badge>
      </div>
      <div className="border-t border-ink-100 px-4 py-3 sm:px-5">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-ink-500">
          {r.kind === "manual_review" ? "Human decision required" : `Measured · ${TOOL[r.kind] ?? "tool"}`}
        </p>
        <p className="mt-1 text-[15px] leading-relaxed text-ink-900">{summarize(r)}</p>
      </div>
      <details className="border-t border-ink-100 px-4 py-2 text-sm sm:px-5">
        <summary className="cursor-pointer py-1 text-xs font-medium text-ink-600">Technical measurements (expected vs actual)</summary>
        <div className="grid gap-4 py-2 md:grid-cols-2">
          <div>
            <p className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-ink-500">Expected</p>
            <Values obj={r.expected} />
          </div>
          <div>
            <p className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-ink-500">Actual measurement</p>
            <Values obj={r.measured} />
          </div>
        </div>
      </details>
      {artifacts.length > 0 && (
        <div className="flex flex-wrap gap-2 border-t border-ink-100 px-4 py-3 sm:px-5">
          {artifacts.map((a) => (
            <ArtifactButton key={a.id} artifact={a} onOpen={onOpen} />
          ))}
        </div>
      )}
      {explanation && (
        <div className="border-t border-ink-100 bg-ink-50/60 px-4 py-3 text-sm sm:px-5">
          <p className="text-xs font-semibold uppercase tracking-wider text-brand-700">
            AI interpretation {explanation.confidence && <span className="font-normal normal-case text-ink-500">· confidence {explanation.confidence}</span>}
            {explanation.insufficient_evidence && <span className="font-normal normal-case text-review-800"> · insufficient evidence</span>}
          </p>
          <p className="mt-1 text-ink-800">{explanation.explanation}</p>
          {explanation.remediation && (
            <p className="mt-2 text-ink-700">
              <span className="font-medium">Suggested remediation: </span>
              {explanation.remediation}
            </p>
          )}
        </div>
      )}
      {r.outcome === "manual_review_required" && criterion && (
        <ManualReviewPanel run={run} result={r} criterion={criterion} buyerOrgId={buyerOrgId} isLatestCompleted={isLatestCompleted} onReviewed={onReviewed} />
      )}
    </li>
  );
}
