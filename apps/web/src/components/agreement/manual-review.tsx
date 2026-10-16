"use client";

import { useState } from "react";
import { api, ApiError } from "@/lib/client/api";
import { useApi } from "@/lib/client/hooks";
import type { Criterion, ManualReview, Run, RunResult } from "@/lib/types";
import { ERROR_HELP } from "@/lib/status";
import { dateTime } from "@/lib/format";
import { useSession } from "../session";
import { Badge, Button, Dialog, Hash, inputCls, Notice } from "../ui";

export const SCRIPTED_MARKER = "SCRIPTED DEMO DECISION";

/**
 * Human review of a blocking manual criterion. Shown to everyone; the decision form only to
 * buyer reviewers/approvers on human accounts. The API is the authority: it rejects
 * providers, service accounts and stale evidence regardless of what this UI shows.
 */
export function ManualReviewPanel({
  run,
  result,
  criterion,
  buyerOrgId,
  isLatestCompleted,
  onReviewed,
}: {
  run: Run;
  result: RunResult;
  criterion: Criterion;
  buyerOrgId: string;
  isLatestCompleted: boolean;
  onReviewed: () => void;
}) {
  const { user, can } = useSession();
  const reviews = useApi<ManualReview[]>(`/verification/runs/${run.id}/manual-reviews`);
  const existing = reviews.data?.find((d) => d.criterion_id === result.criterion_id);
  const mayReview = user.kind === "human" && can(buyerOrgId, "buyer_reviewer", "buyer_approver");
  const instructions = typeof criterion.spec.instructions === "string" ? criterion.spec.instructions : null;
  const [decision, setDecision] = useState<"satisfied" | "not_satisfied" | "">("");
  const [rationale, setRationale] = useState("");
  const [attest, setAttest] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [stale, setStale] = useState(false);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      // Re-read the run: if the evidence changed while this panel was open, stop here.
      const fresh = await api<Run>(`/verification/runs/${run.id}`);
      if (fresh.evidence_snapshot_sha256 !== run.evidence_snapshot_sha256) {
        setStale(true);
        setConfirm(false);
        return;
      }
      await api<ManualReview>(`/verification/runs/${run.id}/manual-reviews`, {
        method: "POST",
        body: { criterion_id: result.criterion_id, decision, rationale: rationale.trim(), evidence_snapshot_sha256: run.evidence_snapshot_sha256 },
      });
      setConfirm(false);
      reviews.reload();
      onReviewed();
    } catch (e) {
      const err = e as ApiError;
      if (err.code === "STALE_EVIDENCE_VIEW") setStale(true);
      setError(err);
      setConfirm(false);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="border-t border-review-100 bg-review-50/60 px-4 py-4 sm:px-5" data-testid="manual-review-panel">
      {existing ? (
        <ExistingDecision d={existing} />
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="review">Human Review Required</Badge>
            <span className="text-sm text-ink-700">No automated tool can establish this requirement.</span>
          </div>
          {instructions && (
            <div className="mt-3 rounded-lg border border-review-100 bg-white px-3 py-2 text-sm">
              <p className="text-xs font-semibold uppercase tracking-wider text-ink-500">Review instructions (from the locked agreement)</p>
              <p className="mt-1 text-ink-800">{instructions}</p>
            </div>
          )}
          <p className="mt-3 text-xs text-ink-600">
            Run <Hash value={run.id} /> · agreement v-hash <Hash value={run.agreement_content_hash} /> · evidence snapshot{" "}
            <Hash value={run.evidence_snapshot_sha256} label="Evidence snapshot" />. Review the delivery at{" "}
            <a href={run.delivery_url} target="_blank" rel="noopener noreferrer" className="break-all font-medium text-brand-700 underline">
              {run.delivery_url}
            </a>{" "}
            and the stored artifacts above. Automated performance scores do not establish accessibility.
          </p>
          {stale && (
            <Notice tone="fail" title="Evidence changed" className="mt-3">
              The evidence for this run changed while the review was open. Your decision was not recorded.{" "}
              <button className="font-medium underline" onClick={() => window.location.reload()}>
                Refresh and review the current evidence
              </button>
            </Notice>
          )}
          {error && !stale && (
            <Notice tone="fail" title={error.code} className="mt-3">
              {ERROR_HELP[error.code] ?? error.message}
            </Notice>
          )}
          {!isLatestCompleted && <Notice tone="review" className="mt-3">Newer evidence exists. Review the latest run instead — payment policy ignores this one.</Notice>}
          {mayReview && isLatestCompleted && !stale ? (
            <form
              className="mt-4 space-y-3"
              onSubmit={(e) => {
                e.preventDefault();
                setConfirm(true);
              }}
            >
              <fieldset>
                <legend className="text-sm font-medium text-ink-800">Your decision</legend>
                <div className="mt-2 flex flex-wrap gap-3">
                  {(["satisfied", "not_satisfied"] as const).map((d) => (
                    <label key={d} className="flex min-h-11 items-center gap-2 rounded-lg border border-ink-200 bg-white px-3 text-sm">
                      <input type="radio" name={`decision-${result.criterion_id}`} value={d} checked={decision === d} onChange={() => setDecision(d)} className="size-4 accent-brand-600" />
                      {d === "satisfied" ? "Satisfied" : "Not satisfied"}
                    </label>
                  ))}
                </div>
              </fieldset>
              <div>
                <label htmlFor={`rat-${result.criterion_id}`} className="text-sm font-medium text-ink-800">
                  Rationale <span className="font-normal text-ink-500">(required, min. 10 characters — what you checked and what you found)</span>
                </label>
                <textarea id={`rat-${result.criterion_id}`} rows={3} maxLength={4000} className={`${inputCls} mt-1.5`} value={rationale} onChange={(e) => setRationale(e.target.value)} />
              </div>
              <label className="flex items-start gap-2 text-sm text-ink-800">
                <input type="checkbox" className="mt-0.5 size-4 accent-brand-600" checked={attest} onChange={(e) => setAttest(e.target.checked)} />
                <span>I personally reviewed this delivery and attest to a requirement the automated system could not establish.</span>
              </label>
              <Button type="submit" disabled={!decision || rationale.trim().length < 10 || !attest}>
                Record decision…
              </Button>
            </form>
          ) : (
            !mayReview && (
              <p className="mt-3 text-sm text-ink-600">
                {user.kind === "service"
                  ? "Service accounts (including AI integrations) cannot decide manual reviews."
                  : "Only the buyer's authorized reviewers can decide this. Providers cannot review their own delivery."}
              </p>
            )
          )}
        </>
      )}
      <Dialog
        open={confirm}
        onClose={() => setConfirm(false)}
        title="Record your review decision?"
        footer={
          <>
            <Button variant="secondary" onClick={() => setConfirm(false)}>
              Cancel
            </Button>
            <Button onClick={submit} busy={busy} variant={decision === "satisfied" ? "primary" : "danger"}>
              Record “{decision === "satisfied" ? "Satisfied" : "Not satisfied"}”
            </Button>
          </>
        }
      >
        <p>
          You are recording <strong>{decision === "satisfied" ? "satisfied" : "not satisfied"}</strong> for “{criterion.title}”, bound to evidence
          snapshot <Hash value={run.evidence_snapshot_sha256} n={16} />.
        </p>
        <p className="mt-3">
          This is a personal attestation under your name. It is append-only: it cannot be edited later. It does not approve payment — the buyer
          approves capture separately.
        </p>
      </Dialog>
    </div>
  );
}

function ExistingDecision({ d }: { d: ManualReview }) {
  const scripted = d.rationale.startsWith(SCRIPTED_MARKER);
  return (
    <div className="space-y-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={d.decision === "satisfied" ? "pass" : "fail"}>{d.decision === "satisfied" ? "Review: satisfied" : "Review: not satisfied"}</Badge>
        {scripted && <Badge tone="review">Scripted demo decision — not a human review</Badge>}
      </div>
      <p className="text-ink-700">
        Recorded {dateTime(d.decided_at)} by {d.reviewer_email ?? d.reviewer_user_id} ({d.reviewer_role.replace("_", " ")}) for evidence snapshot{" "}
        <Hash value={d.evidence_snapshot_sha256} />.
      </p>
      <blockquote className="border-l-2 border-ink-200 pl-3 text-ink-700">{d.rationale}</blockquote>
    </div>
  );
}
