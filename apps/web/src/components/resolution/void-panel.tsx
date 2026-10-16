"use client";

import { useState } from "react";
import { api, ApiError } from "@/lib/client/api";
import { useApi } from "@/lib/client/hooks";
import type { FinPolicy, Order, VoidOp } from "@/lib/types";
import { ERROR_HELP, reasonText, VOID_STATUS } from "@/lib/status";
import { dateTime, money } from "@/lib/format";
import { Badge, Button, Card, CardHeader, Dialog, Field, inputCls, Notice } from "../ui";

/** Cancel an authorized, uncaptured payment (PayPal void). Not a refund: no money moved. */
export function VoidPanel({ order, canVoid, onChange }: { order: Order; canVoid: boolean; onChange: () => void }) {
  const auth = order.authorization!;
  const status = useApi<VoidOp | null>(`/payments/authorizations/${auth.id}/void-status`);
  const showPolicy = canVoid && auth.status === "authorized" && !order.capture;
  const policy = useApi<FinPolicy>(showPolicy ? `/payments/authorizations/${auth.id}/void-policy` : null);
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);
  const op = status.data;

  async function act(kind: "void" | "reconcile") {
    setBusy(true);
    setErr(null);
    try {
      if (kind === "void") await api<VoidOp>(`/payments/authorizations/${auth.id}/void`, { method: "POST", body: { reason: reason.trim() } });
      else await api<VoidOp>(`/voids/${op!.id}/reconcile`, { method: "POST" });
      setOpen(false);
    } catch (e) {
      setErr(e as ApiError);
      setOpen(false);
    } finally {
      setBusy(false);
      status.reload();
      onChange();
    }
  }

  if (!op && !showPolicy) return null;
  const vs = op ? VOID_STATUS[op.status] : null;
  return (
    <Card>
      <CardHeader title="Cancel authorization" subtitle="Cancelling voids the PayPal authorization. It is not a refund: the payment was never captured, so no money reached the provider." />
      <div className="space-y-3 px-4 py-4 sm:px-5">
        {op && vs && (
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <Badge tone={vs.tone}>{vs.label}</Badge>
            <span className="text-ink-500">
              {dateTime(op.updated_at)} · {op.requested_by.replace("user:", "")} · {op.reason}
            </span>
          </div>
        )}
        {op?.status === "unknown" && (
          <Notice tone="review" title="Cancellation outcome unknown">
            Capture is blocked until this is reconciled. Reconciliation reads the authorization from PayPal first.
            {canVoid && (
              <div className="mt-2">
                <Button size="sm" onClick={() => act("reconcile")} busy={busy}>
                  Reconcile with PayPal
                </Button>
              </div>
            )}
          </Notice>
        )}
        {showPolicy && op?.status !== "completed" && policy.data && (
          <>
            {policy.data.decision === "blocked" ? (
              <Notice tone="review" title="Cancellation not available now">
                <ul className="list-disc pl-5">
                  {policy.data.reasons.map((r, i) => (
                    <li key={i}>
                      {reasonText(r.code)} <code className="text-[11px] text-ink-500">{r.code}</code>
                    </li>
                  ))}
                </ul>
              </Notice>
            ) : (
              <Button variant="danger" onClick={() => setOpen(true)}>
                Cancel authorization…
              </Button>
            )}
          </>
        )}
        {err && (
          <Notice tone={err.outcomeUnknown ? "review" : "fail"} title={err.code}>
            {ERROR_HELP[err.code] ?? err.message}
            {err.reasons.map((r, i) => (
              <div key={i}>{reasonText(r.code)}</div>
            ))}
          </Notice>
        )}
      </div>
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        title="Cancel this authorization?"
        footer={
          <>
            <Button variant="secondary" onClick={() => setOpen(false)}>
              Keep it
            </Button>
            <Button variant="danger" busy={busy} disabled={reason.trim().length < 5} onClick={() => act("void")}>
              Cancel authorization
            </Button>
          </>
        }
      >
        <p>
          The PayPal authorization of {money(auth.amount, auth.currency)} is released. This is not a refund — the buyer was never charged and the provider received nothing.
          A cancelled authorization cannot be captured later; a new payment would be needed.
        </p>
        <div className="mt-3">
          <Field label="Reason (recorded in the audit log)" htmlFor="vr">
            <textarea id="vr" rows={2} className={inputCls} value={reason} onChange={(e) => setReason(e.target.value)} />
          </Field>
        </div>
      </Dialog>
    </Card>
  );
}
