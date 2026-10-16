"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, newIdempotencyKey } from "@/lib/client/api";
import { useApi, usePoll } from "@/lib/client/hooks";
import type { Agreement, Capture, CaptureApproval, Order, Policy, RunSummary } from "@/lib/types";
import { AUTH_STATUS, CAPTURE_STATUS, ERROR_HELP, ORDER_STATUS, POLICY, reasonText, VERDICT } from "@/lib/status";
import { dateTime, money, relative } from "@/lib/format";
import { useSession } from "../session";
import { Badge, Button, Card, CardHeader, Dialog, EmptyState, Hash, KV, LoadingBlock, Notice, Spinner } from "../ui";

export function PaymentWorkspace({
  agreement,
  latestRun,
  onChange,
}: {
  agreement: Agreement;
  latestRun: RunSummary | null;
  onChange: () => void;
}) {
  const { user, can } = useSession();
  const org = agreement.buyer_organization_id;
  const human = user.kind === "human";
  const canStart = human && can(org, "buyer_admin");
  const canAuthorize = human && can(org, "buyer_admin", "buyer_approver");
  const canCapture = human && can(org, "buyer_admin", "buyer_approver");
  const orders = useApi<Order[]>(`/agreements/${agreement.id}/payments`);
  const order = orders.data?.[0] ?? null;
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const startKey = useRef<string | null>(null);
  const locked = agreement.current_locked_version_id !== null;
  const version = agreement.versions.find((v) => v.id === agreement.current_locked_version_id);

  const refresh = useCallback(() => {
    orders.reload();
    onChange();
  }, [orders, onChange]);

  async function startCheckout() {
    setBusy("start");
    setError(null);
    // One key per user intent: a double click or a retry after a timeout returns the same order.
    startKey.current ??= newIdempotencyKey("pay");
    try {
      await api<Order>(`/agreements/${agreement.id}/payments`, { method: "POST", idempotencyKey: startKey.current });
      startKey.current = null;
      refresh();
    } catch (e) {
      setError(e as ApiError);
    } finally {
      setBusy(null);
    }
  }

  if (orders.loading && !orders.data) return <LoadingBlock rows={4} />;

  return (
    <div className="space-y-5">
      {error && (
        <Notice tone="fail" title={error.code}>
          {ERROR_HELP[error.code] ?? error.message}
        </Notice>
      )}
      <Card>
        <CardHeader title="PayPal payment" subtitle="Sandbox only. Authorization and capture are separate events." action={order?.simulated ? <Badge tone="review">SIMULATED — rehearsal mode, not PayPal</Badge> : undefined} />
        <div className="px-4 py-4 sm:px-5">
          {!order ? (
            !locked ? (
              <EmptyState title="Lock the agreement first">A payment is always created for a locked agreement version — its amount and payee come from that version.</EmptyState>
            ) : (
              <div className="space-y-3">
                <p className="text-sm text-ink-700">
                  Start a PayPal Sandbox checkout for <strong>{money(version?.amount, version?.currency)}</strong>. The buyer approves at PayPal, then
                  OutcomeGuard <em>authorizes</em> the payment. Nothing is captured until verification, review and buyer approval.
                </p>
                {canStart ? (
                  <Button onClick={startCheckout} busy={busy === "start"}>
                    Start PayPal Sandbox checkout
                  </Button>
                ) : (
                  <p className="text-sm text-ink-500">Only a buyer admin can start the payment.</p>
                )}
              </div>
            )
          ) : (
            <OrderPanel key={`${order.id}:${order.status}:${order.authorization?.status ?? ""}`} order={order} canAuthorize={canAuthorize} onChange={refresh} setError={setError} />
          )}
        </div>
      </Card>
      {order?.authorization && (
        <GovernancePanel
          order={order}
          canCapture={canCapture}
          latestRun={latestRun}
          onChange={refresh}
        />
      )}
    </div>
  );
}

function OrderPanel({
  order,
  canAuthorize,
  onChange,
  setError,
}: {
  order: Order;
  canAuthorize: boolean;
  onChange: () => void;
  setError: (e: ApiError | null) => void;
}) {
  // Remounted (via key) whenever the stored order changes; polling updates this copy.
  const [live, setLive] = useState<Order>(order);
  const [busy, setBusy] = useState(false);
  const [watching, setWatching] = useState(false);
  const os = ORDER_STATUS[live.status] ?? { label: live.status, tone: "neutral" as const };
  // Bounded polling of PayPal (via GET order) while the buyer approves in another tab.
  const poll = usePoll<Order>({
    enabled: watching && live.status === "created",
    subject: live.id,
    fetcher: (signal) => api<Order>(`/payments/orders/${live.id}`, { signal }),
    done: (o) => o.status !== "created",
    onUpdate: setLive,
    intervalMs: 5000,
    maxAttempts: 72, // 6 minutes, then the user re-checks manually
  });

  async function check() {
    setError(null);
    try {
      setLive(await api<Order>(`/payments/orders/${live.id}`));
    } catch (e) {
      setError(e as ApiError);
    }
  }
  async function authorize() {
    setBusy(true);
    setError(null);
    try {
      setLive(await api<Order>(`/payments/orders/${live.id}/authorize`, { method: "POST" }));
      onChange();
    } catch (e) {
      setError(e as ApiError);
    } finally {
      setBusy(false);
    }
  }
  const auth = live.authorization;
  return (
    <div className="space-y-4">
      <KV
        items={[
          ["Amount", money(live.amount, live.currency)],
          ["Order", <Badge key="o" tone={os.tone}>{os.label}</Badge>],
          ["PayPal order", <span key="p" className="font-mono text-xs">{live.paypal_order_id ?? "—"} {live.paypal_status && `· ${live.paypal_status}`}</span>],
          ["Environment", live.simulated ? "Simulated (rehearsal)" : "PayPal Sandbox"],
          ...(auth
            ? ([
                ["Authorization", <Badge key="a" tone={(AUTH_STATUS[auth.status] ?? { tone: "neutral" }).tone}>{AUTH_STATUS[auth.status]?.label ?? auth.status}</Badge>],
                ["PayPal authorization", <span key="pa" className="font-mono text-xs">{auth.paypal_authorization_id}</span>],
                ["Authorization expires", auth.expires_at ? `${dateTime(auth.expires_at)} (${relative(auth.expires_at)})` : "Not reported by PayPal"],
              ] as [React.ReactNode, React.ReactNode][])
            : []),
        ]}
      />
      {live.status === "created" && (
        <div className="space-y-3 rounded-lg border border-ink-200 p-4">
          <p className="text-sm text-ink-700">
            <strong>Step 1 — buyer approval at PayPal.</strong> Open the PayPal Sandbox page and approve with a Sandbox <em>buyer</em> account. The
            approval page is PayPal&apos;s; OutcomeGuard never sees your PayPal credentials.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            {live.approval_url ? (
              <a
                href={live.approval_url}
                target="_blank"
                rel="noopener noreferrer"
                onClick={() => setWatching(true)}
                className="inline-flex min-h-11 items-center rounded-lg bg-brand-600 px-4 text-sm font-semibold text-white hover:bg-brand-700"
              >
                Open PayPal approval ↗
              </a>
            ) : (
              <Notice tone="fail">PayPal returned no approval link for this order.</Notice>
            )}
            <Button variant="secondary" onClick={check}>
              Check approval status
            </Button>
            {poll.polling && (
              <span role="status" className="flex items-center gap-2 text-xs text-ink-500">
                <Spinner /> Watching PayPal for the approval…
              </span>
            )}
            {poll.exhausted && <span className="text-xs text-ink-500">Stopped watching. Use “Check approval status” when you have approved.</span>}
          </div>
        </div>
      )}
      {live.status === "approved" && !auth && (
        <div className="space-y-3 rounded-lg border border-ink-200 p-4">
          <p className="text-sm text-ink-700">
            <strong>Step 2 — authorize.</strong> PayPal reports the order as approved. Authorizing places a hold on the buyer&apos;s funds; it does not
            capture them.
          </p>
          {canAuthorize ? (
            <Button onClick={authorize} busy={busy}>
              Authorize payment
            </Button>
          ) : (
            <p className="text-sm text-ink-500">A buyer admin or approver authorizes the payment.</p>
          )}
        </div>
      )}
      {auth && !live.capture && (
        <Notice tone="info" title="Payment is authorized, not captured. Authorization can expire.">
          The provider has not been paid. Capture requires a passing verification, any required human review, a fresh policy check and a separate
          buyer approval.
        </Notice>
      )}
      {live.status === "cancelled" && <Notice tone="neutral">The buyer cancelled at PayPal. Start a new checkout to try again.</Notice>}
    </div>
  );
}

function GovernancePanel({
  order,
  canCapture,
  latestRun,
  onChange,
}: {
  order: Order;
  canCapture: boolean;
  latestRun: RunSummary | null;
  onChange: () => void;
}) {
  const auth = order.authorization!;
  const capture = order.capture;
  // A fresh server-side evaluation on open, after each capture state change and on demand.
  const [evalTick, setEvalTick] = useState(0);
  const evalKey = `${auth.id}:${capture?.status ?? ""}:${evalTick}`;
  const [policyState, setPolicyState] = useState<{ key: string; policy: Policy | null; error: ApiError | null }>({ key: "", policy: null, error: null });
  const policy = policyState.policy;
  const policyErr = policyState.key === evalKey ? policyState.error : null;
  const evaluating = policyState.key !== evalKey;
  const approvals = useApi<CaptureApproval[]>(`/payments/authorizations/${auth.id}/capture-approvals`);
  const [dialog, setDialog] = useState<"approve" | "capture" | "reconcile" | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionErr, setActionErr] = useState<ApiError | null>(null);
  const [result, setResult] = useState<Capture | null>(null);
  const inFlight = useRef(false);

  const evaluate = useCallback(() => setEvalTick((t) => t + 1), []);
  useEffect(() => {
    const ctl = new AbortController();
    api<Policy>(`/payments/authorizations/${auth.id}/policy`, { signal: ctl.signal })
      .then((p) => setPolicyState({ key: evalKey, policy: p, error: null }))
      .catch((e) => {
        if ((e as Error).name !== "AbortError") setPolicyState((s) => ({ key: evalKey, policy: s.policy, error: e as ApiError }));
      });
    return () => ctl.abort();
  }, [auth.id, evalKey]);

  const usable = approvals.data?.find((a) => !a.consumed_by_capture_id && a.inputs_sha256 === policy?.inputs_sha256);
  const staleApproval = approvals.data?.find((a) => !a.consumed_by_capture_id && policy && a.inputs_sha256 !== policy.inputs_sha256);
  const p = policy ? POLICY[policy.decision] ?? { label: policy.decision, tone: "neutral" as const } : null;
  const cap = result ?? capture;
  const capStatus = cap ? CAPTURE_STATUS[cap.status] ?? { label: cap.status, tone: "neutral" as const } : null;

  async function run(kind: "approve" | "capture" | "reconcile") {
    if (inFlight.current) return; // duplicate-submission guard
    inFlight.current = true;
    setBusy(true);
    setActionErr(null);
    try {
      if (kind === "approve") {
        await api(`/payments/authorizations/${auth.id}/capture-approvals`, { method: "POST", body: { inputs_sha256: policy!.inputs_sha256 } });
        approvals.reload();
      } else if (kind === "capture") {
        setResult(await api<Capture>(`/payments/authorizations/${auth.id}/captures`, { method: "POST", body: { approval_id: usable!.id } }));
      } else {
        setResult(await api<Capture>(`/payments/captures/${cap!.id}/reconcile`, { method: "POST" }));
      }
      setDialog(null);
      onChange();
      approvals.reload();
    } catch (e) {
      const err = e as ApiError;
      setActionErr(err);
      setDialog(null);
      if (err.outcomeUnknown || kind !== "approve") onChange();
      evaluate();
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  return (
    <>
      <Card>
        <CardHeader
          title="Payment policy"
          subtitle="Deterministic rules evaluated by the server. This page never decides eligibility itself."
          action={
            <Button size="sm" variant="secondary" onClick={evaluate} busy={evaluating}>
              Re-evaluate
            </Button>
          }
        />
        <div className="space-y-4 px-4 py-4 sm:px-5">
          {policyErr && <Notice tone="fail" title={policyErr.code}>{policyErr.message}</Notice>}
          {!policy && !policyErr && <LoadingBlock rows={2} />}
          {policy && p && (
            <>
              <div className="flex flex-wrap items-center gap-3">
                <Badge tone={p.tone}>{p.label}</Badge>
                <span className="text-xs text-ink-500">Evaluated {dateTime(policy.evaluated_at)}</span>
              </div>
              {policy.reasons.length > 0 ? (
                <ul className="space-y-2" aria-label="Reasons">
                  {policy.reasons.map((r, i) => (
                    <li key={i} className="flex items-start gap-3 rounded-lg border border-ink-200 px-3 py-2 text-sm">
                      <span className={`mt-1.5 size-2 shrink-0 rounded-full ${r.code === "MANUAL_REVIEW_PENDING" ? "bg-review-700" : "bg-fail-600"}`} aria-hidden />
                      <span>
                        <span className="text-ink-900">{reasonText(r.code)}</span>
                        {r.detail && <span className="text-ink-500"> — {r.detail}</span>}
                        <code className="ml-2 font-mono text-[11px] text-ink-500">{r.code}</code>
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                policy.decision === "eligible_for_approval" && <p className="text-sm text-pass-700">Every condition holds. A buyer approver may approve the capture.</p>
              )}
              <KV
                items={[
                  ["Verification (latest completed)", latestRun?.verdict ? VERDICT[latestRun.verdict]?.label ?? latestRun.verdict : "No completed run"],
                  ["Run evaluated", <Hash key="r" value={policy.verification_run_id} />],
                  ["Evidence snapshot", <Hash key="s" value={policy.evidence_snapshot_sha256} />],
                  ["Policy inputs hash", <Hash key="i" value={policy.inputs_sha256} n={16} />],
                ]}
              />
            </>
          )}
        </div>
      </Card>

      <Card>
        <CardHeader title="Capture" subtitle="Buyer approval and capture are two separate, explicit actions." />
        <div className="space-y-4 px-4 py-4 sm:px-5">
          {actionErr && (
            <Notice tone={actionErr.outcomeUnknown ? "review" : "fail"} title={actionErr.outcomeUnknown ? "PayPal outcome unknown" : actionErr.code}>
              {ERROR_HELP[actionErr.code] ?? actionErr.message}
              {actionErr.reasons.length > 0 && (
                <ul className="mt-2 list-disc pl-5">
                  {actionErr.reasons.map((r, i) => (
                    <li key={i}>{reasonText(r.code)}</li>
                  ))}
                </ul>
              )}
            </Notice>
          )}
          {cap && capStatus && (
            <div className="space-y-3">
              <KV
                items={[
                  ["Capture", <Badge key="c" tone={capStatus.tone}>{capStatus.label}</Badge>],
                  ["Amount", money(cap.amount, cap.currency)],
                  ["PayPal capture", <span key="pc" className="font-mono text-xs">{cap.paypal_capture_id ?? "—"}</span>],
                  ...(cap.status === "completed"
                    ? ([
                        ["PayPal fee (PayPal-reported)", money(cap.settlement.paypal_fee, cap.currency)],
                        ["Net to provider (PayPal-reported)", money(cap.settlement.net_at_capture, cap.currency)],
                      ] as [React.ReactNode, React.ReactNode][])
                    : []),
                ]}
              />
              {cap.status === "unknown" && (
                <Notice tone="review" title="Outcome unknown — do not retry">
                  The capture request reached PayPal but the answer was lost. The money may or may not have moved. OutcomeGuard blocks every new
                  capture attempt until this is reconciled: reconciliation reads PayPal first and reuses the original request id — it never creates a
                  second payment.
                  {canCapture && (
                    <div className="mt-3">
                      <Button size="sm" onClick={() => setDialog("reconcile")}>
                        Reconcile with PayPal…
                      </Button>
                    </div>
                  )}
                </Notice>
              )}
              {cap.status === "completed" &&
                (order.simulated ? (
                  <Notice tone="pass" title="Payment captured (SIMULATED)">
                    Rehearsal mode: the capture was recorded against the simulated gateway. No PayPal call was made and no Sandbox funds moved.
                  </Notice>
                ) : (
                  <Notice tone="pass" title="Payment captured">PayPal reports the capture as completed. The provider has been paid in the Sandbox.</Notice>
                ))}
              {cap.status === "failed" && <Notice tone="fail" title="Capture rejected by PayPal">No money moved. {cap.error ? JSON.stringify(cap.error) : ""}</Notice>}
            </div>
          )}
          {!cap && (
            <>
              {staleApproval && (
                <Notice tone="review" title="An earlier approval no longer matches">
                  It was given for policy inputs <Hash value={staleApproval.inputs_sha256} />. The facts changed since, so it cannot be used.
                </Notice>
              )}
              {usable ? (
                <div className="space-y-3">
                  <Notice tone="pass" title="Capture approved by the buyer">
                    Approved by {usable.approver_email} ({usable.approver_role.replace("_", " ")}) {dateTime(usable.approved_at)}, bound to inputs hash{" "}
                    <Hash value={usable.inputs_sha256} />.
                  </Notice>
                  {canCapture ? (
                    <Button onClick={() => setDialog("capture")} disabled={busy}>
                      Capture {money(auth.amount, auth.currency)} via PayPal…
                    </Button>
                  ) : (
                    <p className="text-sm text-ink-500">A buyer approver or admin executes the capture.</p>
                  )}
                </div>
              ) : policy?.decision === "eligible_for_approval" ? (
                canCapture ? (
                  <Button onClick={() => setDialog("approve")} disabled={busy}>
                    Approve capture…
                  </Button>
                ) : (
                  <p className="text-sm text-ink-500">Eligible. A buyer approver or admin must approve the capture.</p>
                )
              ) : (
                <p className="text-sm text-ink-600">Capture approval becomes available only when the policy says “eligible for buyer approval”.</p>
              )}
            </>
          )}
        </div>
      </Card>

      <Dialog
        open={dialog === "approve"}
        onClose={() => setDialog(null)}
        title="Approve this capture?"
        footer={
          <>
            <Button variant="secondary" onClick={() => setDialog(null)}>
              Cancel
            </Button>
            <Button onClick={() => run("approve")} busy={busy}>
              I approve the capture
            </Button>
          </>
        }
      >
        <KV
          items={[
            ["Amount", money(auth.amount, auth.currency)],
            ["Verification", latestRun?.verdict ? VERDICT[latestRun.verdict]?.label : "—"],
            ["Policy", p?.label ?? "—"],
            ["Bound to inputs hash", <Hash key="h" value={policy?.inputs_sha256} n={16} />],
          ]}
        />
        <p className="mt-4">
          Your approval is bound to exactly these facts. If anything changes before capture — new evidence, a new agreement version, an expired
          authorization — it becomes unusable and the capture is refused. Approving does not capture; capture is the next, separate step.
        </p>
      </Dialog>
      <Dialog
        open={dialog === "capture"}
        onClose={() => setDialog(null)}
        title={`Capture ${money(auth.amount, auth.currency)}?`}
        footer={
          <>
            <Button variant="secondary" onClick={() => setDialog(null)}>
              Cancel
            </Button>
            <Button onClick={() => run("capture")} busy={busy}>
              Capture payment
            </Button>
          </>
        }
      >
        <p>
          The server re-evaluates every policy condition under a lock immediately before calling PayPal. If anything changed, the capture is refused
          and no money moves.
        </p>
        <p className="mt-3">This moves Sandbox funds from the buyer&apos;s authorization to the provider. It cannot be undone from here.</p>
      </Dialog>
      <Dialog
        open={dialog === "reconcile"}
        onClose={() => setDialog(null)}
        title="Reconcile with PayPal?"
        footer={
          <>
            <Button variant="secondary" onClick={() => setDialog(null)}>
              Cancel
            </Button>
            <Button onClick={() => run("reconcile")} busy={busy}>
              Reconcile
            </Button>
          </>
        }
      >
        <p>OutcomeGuard reads the order from PayPal and settles from the capture it finds. Only if PayPal shows none does it replay the original request with the same idempotency key. A second, independent capture is never created.</p>
      </Dialog>
    </>
  );
}
