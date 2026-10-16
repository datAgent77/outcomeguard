"use client";

import { useState } from "react";
import { api, ApiError } from "@/lib/client/api";
import { usePoll } from "@/lib/client/hooks";
import type { Agreement, Run } from "@/lib/types";
import { ERROR_HELP, RUN_STATUS, VERDICT } from "@/lib/status";
import { dateTime, relative } from "@/lib/format";
import { useSession } from "../session";
import { Badge, Button, Card, CardHeader, EmptyState, Field, Hash, inputCls, Notice, Spinner } from "../ui";

const TERMINAL = new Set(["completed", "errored"]);

/** The six states the spec asks to keep apart. An accepted submission is not a verdict. */
function phase(run: Run): { label: string; tone: "info" | "pass" | "fail" | "review" | "neutral"; help: string } {
  if (run.status === "queued") return { label: "Submission accepted — queued", tone: "info", help: "Waiting for a verification worker." };
  if (run.status === "running") return { label: "Verification running", tone: "info", help: "A browser, Lighthouse and the form test are measuring the delivery." };
  if (run.status === "errored") return { label: "Verification inconclusive", tone: "fail", help: run.error ?? "The run could not complete. Nothing was proven." };
  const v = VERDICT[run.verdict ?? ""];
  return v ? { label: `Verification completed — ${v.label.toLowerCase()}`, tone: v.tone, help: v.help } : { label: "Completed", tone: "neutral", help: "" };
}

export function DeliveryPanel({
  agreement,
  runs,
  onRunsChange,
  onOpenRun,
}: {
  agreement: Agreement;
  runs: Run[];
  onRunsChange: () => void;
  onOpenRun: (id: string) => void;
}) {
  const { can } = useSession();
  const [url, setUrl] = useState("");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [live, setLive] = useState<Run | null>(null);
  const locked = agreement.current_locked_version_id !== null;
  const merchantOrgs = useSession().merchantOrgs;
  const canSubmit = merchantOrgs.some((o) => can(o.id, "provider", "merchant_admin"));
  const latest = live && runs[0]?.id === live.id ? live : runs[0] ?? null;

  const poll = usePoll<Run>({
    enabled: !!latest && !TERMINAL.has(latest.status),
    subject: latest?.id ?? "",
    fetcher: (signal) => api<Run>(`/verification/runs/${latest!.id}`, { signal }),
    done: (r) => TERMINAL.has(r.status),
    onUpdate: (r) => {
      setLive(r);
      if (TERMINAL.has(r.status)) onRunsChange();
    },
    intervalMs: 3000,
    maxAttempts: 100, // ~5 minutes; Lighthouse retries can take a while
  });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api<Run>(`/agreements/${agreement.id}/evidence`, { method: "POST", body: { delivery_url: url.trim(), notes: notes.trim() || null } });
      setLive(r);
      setUrl("");
      setNotes("");
      onRunsChange();
    } catch (e) {
      setError(e as ApiError);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      {canSubmit && (
        <Card>
          <CardHeader title="Submit delivery" subtitle="Submitting starts an automated verification of the locked criteria. It does not mean the work passed." />
          {locked ? (
            <form onSubmit={submit} className="space-y-4 px-4 py-4 sm:px-5">
              <Field label="Delivery URL" htmlFor="durl" hint="The page the buyer will verify. The verifier only reaches allowed public (or operator-allowed test) addresses.">
                <input id="durl" type="url" required className={inputCls} value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…" />
              </Field>
              <Field label="Notes for the buyer (optional)" htmlFor="dnotes">
                <textarea id="dnotes" rows={2} maxLength={5000} className={inputCls} value={notes} onChange={(e) => setNotes(e.target.value)} />
              </Field>
              {error && (
                <Notice tone="fail" title={error.code}>
                  {ERROR_HELP[error.code] ?? error.message}
                </Notice>
              )}
              <Button type="submit" busy={busy} disabled={!url.trim()}>
                Submit evidence for verification
              </Button>
            </form>
          ) : (
            <EmptyState title="Not locked yet">Deliveries can be submitted once the buyer locks the agreement.</EmptyState>
          )}
        </Card>
      )}
      <Card>
        <CardHeader title="Verification runs" subtitle={`${runs.length} run${runs.length === 1 ? "" : "s"}`} />
        {latest && !TERMINAL.has(latest.status) && (
          <div className="flex items-center gap-2 border-b border-ink-100 bg-brand-50 px-4 py-3 text-sm text-brand-700 sm:px-5" role="status">
            {poll.polling && <Spinner />}
            {phase(latest).label}. {phase(latest).help}
            {poll.exhausted && (
              <button className="ml-2 font-medium underline" onClick={poll.restart}>
                Still waiting — check again
              </button>
            )}
          </div>
        )}
        {runs.length === 0 ? (
          <EmptyState title="No delivery submitted yet">{canSubmit ? "Submit the delivery URL above." : "The provider has not submitted a delivery."}</EmptyState>
        ) : (
          <ul className="divide-y divide-ink-100">
            {runs.map((r0) => {
              const r = live && live.id === r0.id ? live : r0;
              const ph = phase(r);
              const rs = RUN_STATUS[r.status];
              return (
                <li key={r.id} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-5">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone={rs?.tone ?? "neutral"}>{rs?.label ?? r.status}</Badge>
                      {r.verdict && <Badge tone={VERDICT[r.verdict]?.tone}>{VERDICT[r.verdict]?.label}</Badge>}
                    </div>
                    <p className="mt-1 break-all font-mono text-xs text-ink-600">{r.delivery_url}</p>
                    <p className="mt-0.5 text-xs text-ink-500">
                      {r.finished_at ? `Finished ${dateTime(r.finished_at)}` : r.started_at ? `Started ${relative(r.started_at)}` : "Queued"} · run <Hash value={r.id} />
                    </p>
                    {r.status === "errored" && <p className="mt-1 text-xs text-fail-700">{ph.help}</p>}
                  </div>
                  {TERMINAL.has(r.status) && (
                    <Button size="sm" variant="secondary" onClick={() => onOpenRun(r.id)}>
                      Open report
                    </Button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </div>
  );
}
