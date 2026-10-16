"use client";

import { useRef, useState } from "react";
import { api, ApiError, newIdempotencyKey } from "@/lib/client/api";
import { useApi } from "@/lib/client/hooks";
import type { FinPolicy, Proposal, Refund, ResolutionCase, Run } from "@/lib/types";
import { CASE_STATUS, ERROR_HELP, OUTCOME, REASON_CATEGORY, reasonText, REFUND_STATUS, REMEDY, VERDICT } from "@/lib/status";
import { dateTime, money } from "@/lib/format";
import { useSession } from "../session";
import { Badge, Button, Card, CardHeader, Dialog, ErrorState, Field, Hash, inputCls, KV, LoadingBlock, Notice } from "../ui";

const MONETARY = new Set(["partial_refund", "full_refund", "cancel_authorization"]);
const CLOSED = new Set(["resolved", "closed_no_action", "rejected", "cancelled"]);

/** Two-party case workspace. Every decision is made by the API; this renders it. */
export function CaseWorkspace({ caseId, runs, onChanged }: { caseId: string; runs: Run[]; onChanged: () => void }) {
  const q = useApi<ResolutionCase>(`/resolutions/${caseId}`);
  const [override, setOverride] = useState<{ base: ResolutionCase | undefined; value: ResolutionCase } | null>(null);
  const c = override && override.base === q.data ? override.value : q.data;
  const set = (v: ResolutionCase) => {
    setOverride({ base: q.data, value: v });
    onChanged();
  };
  if (q.error) return <ErrorState error={q.error} onRetry={q.reload} />;
  if (!c) return <LoadingBlock rows={6} label="Loading case" />;
  const st = CASE_STATUS[c.status] ?? { label: c.status, tone: "neutral" as const, help: "" };
  const opener = c.events.find((e) => e.kind === "opened");
  const providerReplies = c.events.filter((e) => e.side === "provider" && (e.kind === "message" || e.kind === "evidence_linked"));
  return (
    <div className="space-y-5" data-testid="case-workspace">
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-3 px-4 py-4 sm:px-5">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-wider text-ink-500">Resolution case</p>
            <h2 className="mt-1 text-lg font-semibold">{REASON_CATEGORY[c.reason_category] ?? c.reason_category}</h2>
            <p className="mt-0.5 text-sm text-ink-600">{st.help}</p>
          </div>
          <Badge tone={st.tone}>{st.label}</Badge>
        </div>
        <div className="grid gap-4 border-t border-ink-100 px-4 py-4 text-sm sm:px-5 md:grid-cols-2">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wider text-ink-500">Buyer position</p>
            <p className="mt-1 text-ink-800">{c.description}</p>
            <p className="mt-1 text-xs text-ink-500">
              {opener?.author_email} · {dateTime(c.created_at)} · requested: {REMEDY[c.requested_remedy]}
              {c.requested_amount && ` (${money(c.requested_amount, c.currency)})`}
            </p>
          </div>
          <div>
            <p className="text-xs font-semibold uppercase tracking-wider text-ink-500">Provider response</p>
            {providerReplies.length ? (
              providerReplies.map((e) => (
                <p key={e.id} className="mt-1 text-ink-800">
                  {e.body ?? (e.kind === "evidence_linked" ? "Linked new evidence." : "")} <span className="text-xs text-ink-500">· {e.author_email}</span>
                </p>
              ))
            ) : (
              <p className="mt-1 text-ink-500">No response yet.</p>
            )}
          </div>
        </div>
        {c.outcome && (
          <div className="border-t border-ink-100 px-4 py-3 text-sm sm:px-5">
            <strong>Outcome:</strong> {c.outcome}
            {c.closure_reason && ` — ${c.closure_reason}`}
          </div>
        )}
      </Card>

      <EvidencePanel c={c} runs={runs} onChange={set} />
      {c.financials && <Financials c={c} onChange={set} reload={q.reload} />}
      <Proposals c={c} onChange={set} />
      <Timeline c={c} onChange={set} />
      <ClosePanel c={c} onChange={set} />
    </div>
  );
}

function useAction(onDone: (c: ResolutionCase) => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const run = async (fn: () => Promise<ResolutionCase>) => {
    setBusy(true);
    setError(null);
    try {
      onDone(await fn());
      return true;
    } catch (e) {
      setError(e as ApiError);
      return false;
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, run };
}

function Err({ e }: { e: ApiError | null }) {
  if (!e) return null;
  return (
    <Notice tone="fail" title={e.code}>
      {ERROR_HELP[e.code] ?? reasonText(e.code) ?? e.message}
      {e.reasons.length > 0 && (
        <ul className="mt-1 list-disc pl-5">
          {e.reasons.map((r, i) => (
            <li key={i}>
              {reasonText(r.code)}
              {r.detail && ` — ${r.detail}`}
            </li>
          ))}
        </ul>
      )}
    </Notice>
  );
}

// ------------------------------------------------------------------ evidence

function EvidencePanel({ c, runs, onChange }: { c: ResolutionCase; runs: Run[]; onChange: (c: ResolutionCase) => void }) {
  const snap = c.evidence_snapshot;
  const linked = c.events.filter((e) => e.kind === "evidence_linked");
  const later = runs.filter((r) => r.status === "completed" && r.id !== snap.verification_run?.id && !linked.some((e) => e.data.verification_run_id === r.id));
  const [runId, setRunId] = useState("");
  const a = useAction(onChange);
  const open = !CLOSED.has(c.status);
  return (
    <Card>
      <CardHeader title="Evidence" subtitle="The snapshot taken when the case was opened is immutable. Later verification runs are added beside it, never over it." />
      <div className="grid gap-4 px-4 py-4 text-sm sm:px-5 md:grid-cols-2">
        <div className="rounded-lg border border-ink-200 p-3">
          <p className="text-xs font-semibold uppercase tracking-wider text-ink-500">At opening</p>
          {snap.verification_run ? (
            <>
              <p className="mt-1">
                Run <Hash value={snap.verification_run.id} /> · {VERDICT[snap.verification_run.verdict ?? ""]?.label ?? snap.verification_run.verdict}
              </p>
              <ul className="mt-2 space-y-1">
                {snap.verification_run.results.map((r) => (
                  <li key={r.criterion_id} className="flex justify-between gap-2">
                    <span className={snap.criteria.some((x) => x.id === r.criterion_id) ? "font-medium" : ""}>{r.key}</span>
                    <Badge tone={OUTCOME[r.outcome]?.tone}>{OUTCOME[r.outcome]?.label ?? r.outcome}</Badge>
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-xs text-ink-500">
                {snap.artifacts.length} artifact(s) referenced · snapshot <Hash value={snap.verification_run.evidence_snapshot_sha256} />
              </p>
              {snap.manual_reviews.map((m) => (
                <p key={m.id} className="mt-1 text-xs text-ink-600">
                  Manual review: {m.decision}
                  {m.scripted && <Badge tone="review" className="ml-1">scripted demo decision</Badge>}
                </p>
              ))}
            </>
          ) : (
            <p className="mt-1 text-ink-500">No verification run existed.</p>
          )}
          {snap.limitations.map((l) => (
            <p key={l} className="mt-1 text-xs text-review-800">
              ⚠ {l}
            </p>
          ))}
          <p className="mt-2 text-xs text-ink-500">
            Case snapshot hash <Hash value={c.evidence_snapshot_sha256} />
          </p>
        </div>
        <div className="rounded-lg border border-ink-200 p-3">
          <p className="text-xs font-semibold uppercase tracking-wider text-ink-500">Added later</p>
          {linked.length ? (
            linked.map((e) => (
              <p key={e.id} className="mt-1">
                Run <Hash value={String(e.data.verification_run_id)} /> · {VERDICT[String(e.data.verdict ?? "")]?.label ?? String(e.data.verdict ?? e.data.run_status)}{" "}
                <span className="text-xs text-ink-500">
                  by {e.author_email} · {dateTime(e.created_at)}
                </span>
                {e.body && <span className="block text-ink-700">{e.body}</span>}
              </p>
            ))
          ) : (
            <p className="mt-1 text-ink-500">Nothing yet. After rework, submit the new delivery in “Delivery &amp; Verification” and link its run here.</p>
          )}
          {open && later.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2">
              <select aria-label="Verification run to link" className="min-h-10 rounded-lg border border-ink-300 px-2 text-sm" value={runId} onChange={(e) => setRunId(e.target.value)}>
                <option value="">Select a later run…</option>
                {later.map((r) => (
                  <option key={r.id} value={r.id}>
                    {dateTime(r.finished_at)} — {r.verdict}
                  </option>
                ))}
              </select>
              <Button size="sm" variant="secondary" disabled={!runId} busy={a.busy} onClick={() => a.run(() => api(`/resolutions/${c.id}/evidence`, { method: "POST", body: { verification_run_id: runId } }))}>
                Link run
              </Button>
            </div>
          )}
          <Err e={a.error} />
        </div>
      </div>
    </Card>
  );
}

// ------------------------------------------------------------------ money

function Financials({ c, onChange, reload }: { c: ResolutionCase; onChange: (c: ResolutionCase) => void; reload: () => void }) {
  const f = c.financials!;
  const { user, can } = useSession();
  const accepted = c.proposals.find((p) => p.status === "accepted" && (p.remedy === "partial_refund" || p.remedy === "full_refund"));
  const policy = useApi<FinPolicy>(accepted ? `/resolutions/${c.id}/policy?proposal_id=${accepted.id}` : null);
  const [dialog, setDialog] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);
  const key = useRef<string | null>(null);
  const inFlight = useRef(false);
  const mayExecute = user.kind === "human" && (c.your_side === "buyer" ? user.organizations.some((o) => o.kind === "buyer" && (o.roles.includes("buyer_admin") || o.roles.includes("buyer_approver"))) : user.organizations.some((o) => o.kind === "merchant" && o.roles.includes("merchant_admin")));
  void can;
  const env = c.evidence_snapshot.payment.environment ?? f.refunds[0]?.environment;

  async function execute() {
    if (inFlight.current || !accepted) return;
    inFlight.current = true;
    setBusy(true);
    setErr(null);
    key.current ??= newIdempotencyKey("refund"); // one key per intent: retries never create a second refund
    try {
      await api<Refund>(`/resolutions/${c.id}/refunds`, { method: "POST", body: { proposal_id: accepted.id }, idempotencyKey: key.current });
      key.current = null;
    } catch (e) {
      setErr(e as ApiError);
    } finally {
      inFlight.current = false;
      setBusy(false);
      setDialog(false);
      onChange(await api<ResolutionCase>(`/resolutions/${c.id}`));
      policy.reload();
    }
  }

  async function reconcile(id: string) {
    setErr(null);
    try {
      await api<Refund>(`/refunds/${id}/reconcile`, { method: "POST" });
    } catch (e) {
      setErr(e as ApiError);
    }
    onChange(await api<ResolutionCase>(`/resolutions/${c.id}`));
  }

  return (
    <Card>
      <CardHeader title="Financial status" subtitle="Figures come from the server; PayPal-reported where marked. Held = refunds reserved, in flight or with an unknown outcome." action={env === "simulated" ? <Badge tone="review">SIMULATED</Badge> : undefined} />
      <div className="space-y-4 px-4 py-4 sm:px-5">
        <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4" data-testid="financials">
          <Fig label="Captured" v={money(f.captured, f.currency)} />
          <Fig label="Refunded" v={money(f.refunded, f.currency)} />
          <Fig label="Held / pending" v={money(f.held, f.currency)} />
          <Fig label="Remaining refundable" v={money(f.remaining, f.currency)} strong />
        </div>
        {f.refunds.length > 0 && (
          <ul className="divide-y divide-ink-100 rounded-lg border border-ink-200 text-sm">
            {f.refunds.map((r) => {
              const s = REFUND_STATUS[r.status] ?? { label: r.status, tone: "neutral" as const };
              return (
                <li key={r.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
                  <span>
                    {money(r.amount, r.currency)} · <span className="font-mono text-xs">{r.paypal_refund_id ?? "no PayPal id yet"}</span>
                    {r.paypal_fee_returned != null && <span className="block text-xs text-ink-500">PayPal-reported fee returned {money(r.paypal_fee_returned, r.currency)} · merchant debit {money(r.merchant_debit, r.currency)}</span>}
                  </span>
                  <span className="flex items-center gap-2">
                    <Badge tone={s.tone}>{s.label}</Badge>
                    {(r.status === "unknown" || r.status === "requested") && mayExecute && (
                      <Button size="sm" variant="secondary" onClick={() => reconcile(r.id)}>
                        Reconcile with PayPal
                      </Button>
                    )}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
        {f.refunds.some((r) => r.status === "unknown" || r.status === "reconciliation_required") && (
          <Notice tone="review" title="A refund's outcome is unknown">
            PayPal&apos;s answer was lost. Do not retry: reconciliation reads PayPal first and reuses the original request key. The amount stays reserved until it is resolved;
            “operator reconciliation required” means it is too old for a safe automatic replay.
          </Notice>
        )}
        {accepted && (
          <div className="space-y-3 rounded-lg border border-ink-200 p-3">
            <p className="text-sm">
              Agreed refund: <strong>{money(accepted.amount, accepted.currency)}</strong> (proposal #{accepted.number}, approved by{" "}
              {accepted.approvals.filter((a) => a.choice === "approve").map((a) => `${a.approver_email} (${a.side})`).join(" and ")})
            </p>
            {policy.data && (
              <>
                <Badge tone={policy.data.decision === "allowed" ? "pass" : "fail"}>{policy.data.decision === "allowed" ? "Refund policy satisfied" : "Refund blocked by policy"}</Badge>
                {policy.data.reasons.length > 0 && (
                  <ul className="list-disc pl-5 text-sm">
                    {policy.data.reasons.map((r, i) => (
                      <li key={i}>
                        {reasonText(r.code)} <code className="text-[11px] text-ink-500">{r.code}</code>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
            {mayExecute ? (
              <Button onClick={() => setDialog(true)} disabled={policy.data?.decision !== "allowed" || busy}>
                Execute refund…
              </Button>
            ) : (
              <p className="text-sm text-ink-500">A buyer approver/admin or the provider&apos;s admin executes the agreed refund.</p>
            )}
          </div>
        )}
        <Err e={err} />
        <button className="text-xs text-brand-700 underline" onClick={reload}>
          Refresh
        </button>
      </div>
      {accepted && (
        <Dialog
          open={dialog}
          onClose={() => setDialog(false)}
          title={`Refund ${money(accepted.amount, accepted.currency)}?`}
          footer={
            <>
              <Button variant="secondary" onClick={() => setDialog(false)}>
                Cancel
              </Button>
              <Button onClick={execute} busy={busy}>
                Refund {money(accepted.amount, accepted.currency)}
              </Button>
            </>
          }
        >
          <KV
            items={[
              ["Original capture", money(f.captured, f.currency)],
              ["Already refunded", money(f.refunded, f.currency)],
              ["Held / pending", money(f.held, f.currency)],
              ["Remaining refundable", money(f.remaining, f.currency)],
              ["This refund", <strong key="a">{money(accepted.amount, accepted.currency)}</strong>],
              ["Approved by", accepted.approvals.filter((a) => a.choice === "approve").map((a) => `${a.approver_email} (${a.side})`).join(", ")],
              ["Bound to proposal", <Hash key="h" value={accepted.content_sha256} n={16} />],
            ]}
          />
          <p className="mt-4">
            The server re-checks the refund policy under a lock right before calling PayPal. PayPal fees are not assumed to be returned; only PayPal-reported figures are shown
            afterwards. This cannot be undone from here.
          </p>
        </Dialog>
      )}
    </Card>
  );
}

function Fig({ label, v, strong }: { label: string; v: string; strong?: boolean }) {
  return (
    <div className="rounded-lg border border-ink-200 px-3 py-2">
      <div className="text-xs text-ink-500">{label}</div>
      <div className={strong ? "text-base font-semibold tabular-nums" : "font-medium tabular-nums"}>{v}</div>
    </div>
  );
}

// ------------------------------------------------------------------ proposals

function Proposals({ c, onChange }: { c: ResolutionCase; onChange: (c: ResolutionCase) => void }) {
  const { user } = useSession();
  const a = useAction(onChange);
  const [confirm, setConfirm] = useState<{ p: Proposal; choice: "approve" | "reject" } | null>(null);
  const [remedy, setRemedy] = useState("partial_refund");
  const [amount, setAmount] = useState("");
  const [rationale, setRationale] = useState("");
  const [approve, setApprove] = useState(true);
  const side = c.your_side;
  const roles = user.organizations.filter((o) => (side === "buyer" ? o.kind === "buyer" : o.kind === "merchant")).flatMap((o) => o.roles);
  const finance = side === "buyer" ? roles.some((r) => r === "buyer_admin" || r === "buyer_approver") : roles.some((r) => r === "merchant_admin" || r === "merchant_approver");
  const canPropose = user.kind === "human" && (side === "buyer" ? finance : roles.length > 0);
  const open = !CLOSED.has(c.status) && c.status !== "executing";
  const remedies = ["rework", "new_verification", ...(c.payment_capture_id ? ["partial_refund", "full_refund"] : []), ...(c.payment_authorization_id && !c.payment_capture_id ? ["cancel_authorization"] : []), "other"];
  const monetary = MONETARY.has(remedy);

  return (
    <Card>
      <CardHeader title="Proposals" subtitle="Every proposal is recorded permanently. A remedy is agreed only when the buyer and the provider each approve exactly this version." />
      <div className="space-y-3 px-4 py-4 sm:px-5">
        {c.proposals.length === 0 && <p className="text-sm text-ink-500">No proposal yet.</p>}
        {[...c.proposals].reverse().map((p) => {
          const mine = p.approvals.find((x) => x.side === side);
          const mayDecide = user.kind === "human" && p.status === "open" && !mine && (MONETARY.has(p.remedy) ? finance : side === "buyer" ? finance : roles.length > 0);
          return (
            <article key={p.id} className="rounded-lg border border-ink-200 p-3 text-sm" data-testid={`proposal-${p.number}`}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-medium">
                  #{p.number} · {REMEDY[p.remedy]}
                  {p.amount && ` · ${money(p.amount, p.currency)}`}
                </span>
                <Badge tone={p.status === "accepted" || p.status === "executed" ? "pass" : p.status === "open" ? "review" : "neutral"}>{p.status}</Badge>
              </div>
              <p className="mt-1 text-ink-700">{p.rationale}</p>
              <p className="mt-1 text-xs text-ink-500">
                Proposed by {p.author_email} ({p.side}) · {dateTime(p.created_at)} · version <Hash value={p.content_sha256} />
              </p>
              <div className="mt-2 flex flex-wrap gap-2 text-xs">
                {(["buyer", "provider"] as const).map((s) => {
                  const d = p.approvals.find((x) => x.side === s);
                  return (
                    <Badge key={s} tone={d ? (d.choice === "approve" ? "pass" : "fail") : "neutral"}>
                      {s}: {d ? `${d.choice}d by ${d.approver_email}` : "pending"}
                    </Badge>
                  );
                })}
              </div>
              {mayDecide && open && (
                <div className="mt-3 flex gap-2">
                  <Button size="sm" onClick={() => setConfirm({ p, choice: "approve" })}>
                    Approve as {side}
                  </Button>
                  <Button size="sm" variant="secondary" onClick={() => setConfirm({ p, choice: "reject" })}>
                    Reject
                  </Button>
                </div>
              )}
            </article>
          );
        })}
        <Err e={a.error} />
        {open && canPropose && (
          <form
            className="space-y-3 rounded-lg border border-dashed border-ink-300 p-3"
            onSubmit={async (e) => {
              e.preventDefault();
              const ok = await a.run(() =>
                api(`/resolutions/${c.id}/proposals`, {
                  method: "POST",
                  body: { expected_revision: c.revision, remedy, amount: remedy === "partial_refund" ? amount : null, rationale: rationale.trim(), approve: approve && (finance || !monetary) },
                }),
              );
              if (ok) {
                setRationale("");
                setAmount("");
              }
            }}
          >
            <p className="text-sm font-medium">New proposal ({side})</p>
            <Field label="Remedy" htmlFor="rem">
              <select id="rem" className={inputCls} value={remedy} onChange={(e) => setRemedy(e.target.value)}>
                {remedies.filter((r) => side === "buyer" || finance || !MONETARY.has(r)).map((r) => (
                  <option key={r} value={r}>
                    {REMEDY[r]}
                  </option>
                ))}
              </select>
            </Field>
            {remedy === "partial_refund" && (
              <Field label={`Amount (${c.currency})`} htmlFor="pam" hint={c.financials ? `Remaining refundable: ${money(c.financials.remaining, c.currency)}` : undefined}>
                <input id="pam" inputMode="decimal" className={inputCls} value={amount} onChange={(e) => setAmount(e.target.value.trim())} />
              </Field>
            )}
            {remedy === "full_refund" && <p className="text-xs text-ink-500">A full refund is the remaining refundable balance, computed by the server.</p>}
            <Field label="Rationale" htmlFor="rat" hint="At least 10 characters.">
              <textarea id="rat" rows={3} className={inputCls} value={rationale} onChange={(e) => setRationale(e.target.value)} />
            </Field>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" className="size-4 accent-brand-600" checked={approve} onChange={(e) => setApprove(e.target.checked)} />
              Also record my approval as {side}
            </label>
            <Button type="submit" busy={a.busy} disabled={rationale.trim().length < 10 || (remedy === "partial_refund" && !/^\d{1,7}(\.\d{1,2})?$/.test(amount))}>
              Submit proposal
            </Button>
            <p className="text-xs text-ink-500">A new proposal supersedes any open one. AI never submits or approves proposals.</p>
          </form>
        )}
      </div>
      <Dialog
        open={!!confirm}
        onClose={() => setConfirm(null)}
        title={confirm?.choice === "approve" ? `Approve proposal #${confirm.p.number}?` : `Reject proposal #${confirm?.p.number}?`}
        footer={
          <>
            <Button variant="secondary" onClick={() => setConfirm(null)}>
              Cancel
            </Button>
            <Button
              variant={confirm?.choice === "reject" ? "danger" : "primary"}
              busy={a.busy}
              onClick={async () => {
                if (!confirm) return;
                await a.run(() => api(`/resolutions/${c.id}/proposals/${confirm.p.id}/${confirm.choice}`, { method: "POST", body: { content_sha256: confirm.p.content_sha256 } }));
                setConfirm(null);
              }}
            >
              {confirm?.choice === "approve" ? "Approve" : "Reject"}
            </Button>
          </>
        }
      >
        {confirm && (
          <>
            <p>
              {REMEDY[confirm.p.remedy]}
              {confirm.p.amount && ` — ${money(confirm.p.amount, confirm.p.currency)}`}. Your decision is bound to version <Hash value={confirm.p.content_sha256} n={16} /> and
              recorded under your name for the {side} side.
            </p>
            {confirm.choice === "approve" && MONETARY.has(confirm.p.remedy) && (
              <p className="mt-3">Approving does not move money by itself. When both sides have approved, an authorized person executes it after a fresh policy check.</p>
            )}
          </>
        )}
      </Dialog>
    </Card>
  );
}

// ------------------------------------------------------------------ timeline + messages

const KIND: Record<string, string> = {
  opened: "Case opened",
  message: "Message",
  evidence_linked: "Evidence linked",
  proposal_submitted: "Proposal submitted",
  proposal_approved: "Proposal approved",
  proposal_rejected: "Proposal rejected",
  proposal_withdrawn: "Proposal withdrawn",
  status_changed: "Status changed",
  refund_requested: "Refund sent to PayPal",
  refund_completed: "Refund completed (PayPal)",
  refund_unknown: "Refund outcome unknown",
  refund_failed: "Refund rejected by PayPal",
  refund_reconciliation_required: "Refund needs operator reconciliation",
  void_requested: "Cancellation sent to PayPal",
  void_completed: "Authorization cancelled (PayPal)",
  void_unknown: "Cancellation outcome unknown",
  closed: "Case closed",
};

function Timeline({ c, onChange }: { c: ResolutionCase; onChange: (c: ResolutionCase) => void }) {
  const [body, setBody] = useState("");
  const a = useAction(onChange);
  const { user } = useSession();
  return (
    <Card>
      <CardHeader title="Timeline" subtitle="Append-only. Nothing here can be edited or deleted." />
      <ol className="space-y-3 px-4 py-4 text-sm sm:px-5">
        {c.events.map((e) => (
          <li key={e.id} className="border-l-2 border-ink-200 pl-3">
            <div className="flex flex-wrap justify-between gap-2">
              <span className="font-medium">{KIND[e.kind] ?? e.kind}</span>
              <time className="text-xs text-ink-500">{dateTime(e.created_at)}</time>
            </div>
            <p className="text-xs text-ink-500">
              {e.side === "system" ? "System" : `${e.author_email ?? ""} (${e.side})`}
              {e.kind === "status_changed" && ` · ${String(e.data.from)} → ${String(e.data.to)}`}
              {typeof e.data.amount === "string" && ` · ${e.data.amount}`}
            </p>
            {e.body && <p className="mt-0.5 text-ink-800">{e.body}</p>}
          </li>
        ))}
      </ol>
      {!CLOSED.has(c.status) && c.your_side && user.kind === "human" && (
        <form
          className="flex flex-col gap-2 border-t border-ink-100 px-4 py-3 sm:flex-row sm:px-5"
          onSubmit={async (e) => {
            e.preventDefault();
            if (await a.run(() => api(`/resolutions/${c.id}/messages`, { method: "POST", body: { body } }))) setBody("");
          }}
        >
          <label className="sr-only" htmlFor="msg">
            Message
          </label>
          <input id="msg" className={inputCls} placeholder={`Write to the other party as ${c.your_side}…`} value={body} onChange={(e) => setBody(e.target.value)} />
          <Button type="submit" variant="secondary" busy={a.busy} disabled={!body.trim()}>
            Send
          </Button>
        </form>
      )}
      <div className="px-4 pb-3 sm:px-5">
        <Err e={a.error} />
      </div>
    </Card>
  );
}

function ClosePanel({ c, onChange }: { c: ResolutionCase; onChange: (c: ResolutionCase) => void }) {
  const a = useAction(onChange);
  const [reason, setReason] = useState("");
  const [outcome, setOutcome] = useState<string | null>(null);
  if (CLOSED.has(c.status)) return null;
  const agreedNonMonetary = c.status === "agreed" && c.proposals.some((p) => p.status === "accepted" && !MONETARY.has(p.remedy));
  const options: [string, string][] = [
    ...(agreedNonMonetary ? ([["resolved", "Mark the agreed remedy as carried out"]] as [string, string][]) : []),
    ...(c.your_side === "buyer" && ["open", "under_review", "proposed"].includes(c.status)
      ? ([
          ["closed_no_action", "Close without a remedy"],
          ["cancelled", "Withdraw the case"],
          ...(c.status !== "open" ? ([["rejected", "Accept the provider's rejection"]] as [string, string][]) : []),
        ] as [string, string][])
      : []),
    ...(c.your_side === "buyer" && c.status === "agreed" ? ([["closed_no_action", "Close without carrying out the remedy"]] as [string, string][]) : []),
  ];
  if (!options.length) return null;
  return (
    <Card>
      <CardHeader title="Close the case" />
      <div className="flex flex-wrap gap-2 px-4 py-4 sm:px-5">
        {options.map(([o, l]) => (
          <Button key={o} size="sm" variant="secondary" onClick={() => setOutcome(o)}>
            {l}
          </Button>
        ))}
        <Err e={a.error} />
      </div>
      <Dialog
        open={!!outcome}
        onClose={() => setOutcome(null)}
        title="Close this case?"
        footer={
          <>
            <Button variant="secondary" onClick={() => setOutcome(null)}>
              Cancel
            </Button>
            <Button
              busy={a.busy}
              disabled={reason.trim().length < 5}
              onClick={async () => {
                if (await a.run(() => api(`/resolutions/${c.id}/close`, { method: "POST", body: { outcome, reason: reason.trim() } }))) setOutcome(null);
              }}
            >
              Close case
            </Button>
          </>
        }
      >
        <Field label="Reason (recorded in the timeline and audit log)" htmlFor="cr">
          <textarea id="cr" rows={3} className={inputCls} value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
        <p className="mt-2 text-xs text-ink-500">A closed case cannot be reopened. Closing moves no money.</p>
      </Dialog>
    </Card>
  );
}
