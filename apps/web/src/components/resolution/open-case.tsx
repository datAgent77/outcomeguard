"use client";

import { useState } from "react";
import { api, ApiError } from "@/lib/client/api";
import type { Agreement, Order, ResolutionCase, Run } from "@/lib/types";
import { ERROR_HELP, REASON_CATEGORY, REMEDY } from "@/lib/status";
import { money } from "@/lib/format";
import { Badge, Button, Card, CardHeader, cx, Field, inputCls, Notice } from "../ui";

const STEPS = ["Payment", "Issue", "Criteria", "Evidence", "Remedy", "Review"] as const;

/** Guided form. Opening a case never moves money — stated on every step. */
export function OpenCase({
  agreement,
  order,
  run,
  onOpened,
  onCancel,
}: {
  agreement: Agreement;
  order: Order | null;
  run: Run | null;
  onOpened: (c: ResolutionCase) => void;
  onCancel: () => void;
}) {
  const version = agreement.versions.find((v) => v.id === agreement.current_locked_version_id)!;
  const captured = order?.capture?.status === "completed";
  const uncaptured = !!order?.authorization && !order.capture && order.authorization.status === "authorized";
  const [step, setStep] = useState(0);
  const [category, setCategory] = useState(captured ? "defect_after_acceptance" : "cancel_before_capture");
  const [description, setDescription] = useState("");
  const [criteria, setCriteria] = useState<Set<string>>(new Set());
  const [artifacts, setArtifacts] = useState<Set<string>>(new Set());
  const [remedy, setRemedy] = useState(captured ? "partial_refund" : uncaptured ? "cancel_authorization" : "rework");
  const [amount, setAmount] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const remedies = [
    "rework",
    "new_verification",
    ...(captured ? ["partial_refund", "full_refund"] : []),
    ...(uncaptured ? ["cancel_authorization"] : []),
    "other",
  ];
  const amountOk = remedy !== "partial_refund" || /^\d{1,7}(\.\d{1,2})?$/.test(amount);
  const stepOk = [true, description.trim().length >= 10, true, true, amountOk, true][step];

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const c = await api<ResolutionCase>(`/agreements/${agreement.id}/resolutions`, {
        method: "POST",
        body: {
          reason_category: category,
          description: description.trim(),
          requested_remedy: remedy,
          requested_amount: remedy === "partial_refund" ? amount : null,
          criterion_ids: [...criteria],
          artifact_ids: [...artifacts],
        },
      });
      onOpened(c);
    } catch (e) {
      setError(e as ApiError);
    } finally {
      setBusy(false);
    }
  }

  const toggle = (set: Set<string>, setter: (s: Set<string>) => void, id: string) => {
    const n = new Set(set);
    if (n.has(id)) n.delete(id);
    else n.add(id);
    setter(n);
  };

  return (
    <Card>
      <CardHeader title="Open a resolution case" subtitle="Opening a case does not reverse or refund any payment. Money moves only after both parties approve a remedy." />
      <div className="space-y-4 px-4 py-4 sm:px-5">
        <ol className="grid grid-cols-3 gap-1.5 sm:grid-cols-6" aria-label="Progress">
          {STEPS.map((s, i) => (
            <li
              key={s}
              aria-current={i === step ? "step" : undefined}
              className={cx("rounded-md border px-2 py-1 text-xs", i === step ? "border-brand-500 bg-brand-50 text-brand-700" : i < step ? "border-pass-100 bg-pass-50 text-pass-700" : "border-ink-200 text-ink-500")}
            >
              {i + 1}. {s}
            </li>
          ))}
        </ol>

        {step === 0 && (
          <div className="space-y-2 text-sm">
            <p>
              Agreement <strong>{version.title}</strong> · v{version.version_number} · {money(version.amount, version.currency)}
            </p>
            {order ? (
              <p>
                Payment: {order.capture ? `captured ${money(order.capture.amount, order.capture.currency)} (PayPal ${order.capture.paypal_capture_id ?? "—"})` : order.authorization ? `authorized, not captured (PayPal ${order.authorization.paypal_authorization_id})` : "not authorized yet"}
                {order.simulated && <Badge tone="review" className="ml-2">SIMULATED</Badge>}
              </p>
            ) : (
              <p className="text-ink-600">No payment exists yet — only non-monetary remedies are possible.</p>
            )}
          </div>
        )}
        {step === 1 && (
          <div className="space-y-4">
            <Field label="What kind of problem is it?" htmlFor="cat">
              <select id="cat" className={inputCls} value={category} onChange={(e) => setCategory(e.target.value)}>
                {Object.entries(REASON_CATEGORY).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Describe the issue" htmlFor="desc" hint="At least 10 characters. Say what you expected and what you observed.">
              <textarea id="desc" rows={4} maxLength={5000} className={inputCls} value={description} onChange={(e) => setDescription(e.target.value)} />
            </Field>
          </div>
        )}
        {step === 2 && (
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium">Which acceptance criteria are affected? (optional)</legend>
            {version.criteria.map((c) => (
              <label key={c.id} className="flex min-h-11 items-center gap-2 rounded-lg border border-ink-200 px-3 text-sm">
                <input type="checkbox" className="size-4 accent-brand-600" checked={criteria.has(c.id)} onChange={() => toggle(criteria, setCriteria, c.id)} />
                {c.title}
              </label>
            ))}
          </fieldset>
        )}
        {step === 3 && (
          <div className="space-y-2">
            {run ? (
              <>
                <p className="text-sm text-ink-600">
                  The case keeps an immutable snapshot of the latest verification ({run.verdict ?? run.status}, run {run.id.slice(0, 8)}…). Select the artifacts you rely on; if
                  you select none, all of them are referenced.
                </p>
                {run.artifacts.map((a) => (
                  <label key={a.id} className="flex min-h-11 items-center gap-2 rounded-lg border border-ink-200 px-3 text-sm">
                    <input type="checkbox" className="size-4 accent-brand-600" checked={artifacts.has(a.id)} onChange={() => toggle(artifacts, setArtifacts, a.id)} />
                    {a.kind} · {a.label}
                  </label>
                ))}
              </>
            ) : (
              <Notice tone="review">No completed verification exists. The case will record this limitation.</Notice>
            )}
          </div>
        )}
        {step === 4 && (
          <div className="space-y-3">
            <fieldset className="space-y-2">
              <legend className="text-sm font-medium">What remedy do you request?</legend>
              {remedies.map((r) => (
                <label key={r} className="flex min-h-11 items-center gap-2 rounded-lg border border-ink-200 px-3 text-sm">
                  <input type="radio" name="remedy" className="size-4 accent-brand-600" checked={remedy === r} onChange={() => setRemedy(r)} />
                  {REMEDY[r]}
                </label>
              ))}
            </fieldset>
            {remedy === "partial_refund" && (
              <Field label={`Requested amount (${version.currency})`} htmlFor="amt" hint={`Captured: ${money(order?.capture?.amount, order?.capture?.currency)}`}>
                <input id="amt" inputMode="decimal" className={inputCls} value={amount} onChange={(e) => setAmount(e.target.value.trim())} />
              </Field>
            )}
            <p className="text-xs text-ink-500">This is a request, not an instruction. The provider can agree, reject or counter-propose.</p>
          </div>
        )}
        {step === 5 && (
          <div className="space-y-2 text-sm">
            <p>
              <strong>{REASON_CATEGORY[category]}</strong> — {description}
            </p>
            <p>Criteria: {criteria.size ? version.criteria.filter((c) => criteria.has(c.id)).map((c) => c.title).join(", ") : "none selected"}</p>
            <p>Evidence: {artifacts.size ? `${artifacts.size} artifact(s)` : run ? "all artifacts of the latest run" : "none available"}</p>
            <p>
              Requested remedy: {REMEDY[remedy]}
              {remedy === "partial_refund" && ` — ${money(amount, version.currency)}`}
            </p>
            <Notice tone="info">Submitting opens the case. Nothing is refunded or cancelled until both parties approve a proposal and the policy allows it.</Notice>
          </div>
        )}
        {error && (
          <Notice tone="fail" title={error.code}>
            {ERROR_HELP[error.code] ?? error.message}
          </Notice>
        )}
        <div className="flex justify-between gap-2">
          <Button variant="secondary" onClick={step === 0 ? onCancel : () => setStep(step - 1)}>
            {step === 0 ? "Cancel" : "Back"}
          </Button>
          {step < 5 ? (
            <Button onClick={() => setStep(step + 1)} disabled={!stepOk}>
              Continue
            </Button>
          ) : (
            <Button onClick={submit} busy={busy}>
              Open case
            </Button>
          )}
        </div>
      </div>
    </Card>
  );
}
