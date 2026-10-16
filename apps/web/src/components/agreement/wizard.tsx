"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "@/lib/client/api";
import { useApi } from "@/lib/client/hooks";
import type { Agreement, Draft, DraftCriterion, Merchant } from "@/lib/types";
import { AI_OUTCOME, ERROR_HELP, KIND_LABEL, KIND_PLAIN } from "@/lib/status";
import { useSession } from "../session";
import { Badge, Button, Card, CardHeader, cx, EmptyState, Field, inputCls, Notice, Spinner } from "../ui";
import { LockPanel } from "./lock-panel";

const EXAMPLE =
  "Build a mobile-friendly landing page for $500. The contact form must work, the website must be accessible and Lighthouse performance must score at least 90.";

const STEPS = ["Describe the work", "Review criteria", "Configure verification", "Review and lock"] as const;

type Cfg = Record<string, Record<string, unknown>>;

/** Buyer-supplied configuration per criterion kind. Defaults are documented, never invented
 *  selectors, URLs or thresholds: those stay empty until the buyer fills them in. */
function defaultCfg(c: DraftCriterion): Record<string, unknown> {
  switch (c.kind) {
    case "http_availability":
      return { path: "/", require_https: true, expected_status: "200" };
    case "mobile_viewport":
      return { path: "/", width: 390, height: 844, required_visible_selectors: "" };
    case "contact_form":
      return { path: "/", form_selector: "", submit_selector: "", success_text: "", fields: [{ selector: "", value: "" }] };
    case "lighthouse_performance":
      return {
        path: "/",
        min_score: typeof c.expected.min_performance_score === "number" ? c.expected.min_performance_score : "",
        form_factor: typeof c.expected.form_factor === "string" ? c.expected.form_factor : "mobile",
      };
    case "manual_review":
      return { instructions: c.manual_review_instructions ?? c.source_quote };
  }
}

function toApiConfig(c: DraftCriterion, raw: Record<string, unknown>): { config: Record<string, unknown>; missing: string[] } {
  const missing: string[] = [];
  const path = String(raw.path ?? "/").trim() || "/";
  switch (c.kind) {
    case "http_availability": {
      const codes = String(raw.expected_status ?? "")
        .split(/[,\s]+/)
        .filter(Boolean)
        .map(Number);
      if (!codes.length || codes.some((n) => !Number.isInteger(n))) missing.push("expected HTTP status");
      return { config: { path, require_https: !!raw.require_https, expected_status: codes }, missing };
    }
    case "mobile_viewport": {
      const sel = String(raw.required_visible_selectors ?? "")
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean);
      return { config: { path, viewport: { width: Number(raw.width), height: Number(raw.height) }, required_visible_selectors: sel }, missing };
    }
    case "contact_form": {
      const fields = ((raw.fields as { selector: string; value: string }[]) ?? []).filter((f) => f.selector.trim());
      if (!fields.length) missing.push("at least one form field selector");
      if (!String(raw.form_selector ?? "").trim()) missing.push("form selector");
      if (!String(raw.submit_selector ?? "").trim()) missing.push("submit button selector");
      if (!String(raw.success_text ?? "").trim()) missing.push("success message");
      return {
        config: {
          path,
          form_selector: String(raw.form_selector ?? "").trim(),
          submit_selector: String(raw.submit_selector ?? "").trim(),
          success_text: String(raw.success_text ?? "").trim(),
          fields: fields.map((f) => ({ selector: f.selector.trim(), value: f.value })),
        },
        missing,
      };
    }
    case "lighthouse_performance": {
      const n = Number(raw.min_score);
      if (raw.min_score === "" || !Number.isInteger(n) || n < 0 || n > 100) missing.push("minimum performance score (0–100)");
      return { config: { path, min_score: n, form_factor: raw.form_factor }, missing };
    }
    case "manual_review": {
      const instructions = String(raw.instructions ?? "").trim();
      if (!instructions) missing.push("review instructions");
      return { config: { instructions }, missing };
    }
  }
}

export function AgreementWizard() {
  const router = useRouter();
  const params = useSearchParams();
  const { buyerOrgs } = useSession();
  const adminOrgs = buyerOrgs.filter((o) => o.roles.includes("buyer_admin"));
  const [step, setStep] = useState(0);
  const [orgId, setOrgId] = useState(adminOrgs[0]?.id ?? "");
  const [text, setText] = useState("");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  // review decisions
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  const [accepted, setAccepted] = useState<Set<string>>(new Set());
  const [weakened, setWeakened] = useState<Set<string>>(new Set());
  // configuration
  const [cfg, setCfg] = useState<Cfg>({});
  const [title, setTitle] = useState("");
  const [merchantId, setMerchantId] = useState("");
  const [amount, setAmount] = useState("");
  const [deadline, setDeadline] = useState("");
  const [agreement, setAgreement] = useState<Agreement | null>(null);
  const merchants = useApi<Merchant[]>("/workspace/merchants");

  // Resume a draft from the URL (?draft=) so a reload does not lose the AI proposal.
  const draftParam = params.get("draft");
  useEffect(() => {
    if (!draftParam || draft) return;
    api<Draft>(`/agreements/drafts/${draftParam}`)
      .then((d) => {
        setDraft(d);
        setText(d.requirements_text);
        if (d.status === "proposed") setStep(1);
      })
      .catch((e) => setError(e as ApiError));
  }, [draftParam, draft]);

  const stated = useMemo(() => draft?.review.criteria ?? [], [draft]);
  const suggestions = useMemo(() => draft?.review.suggestions ?? [], [draft]);
  const chosen = useMemo(
    () => [...stated.filter((c) => !excluded.has(c.key)), ...suggestions.filter((c) => accepted.has(c.key))],
    [stated, suggestions, excluded, accepted],
  );

  // A single available provider is preselected; the buyer can still see and change it.
  const merchant = merchantId || (merchants.data?.length === 1 ? merchants.data[0].id : "");

  if (!adminOrgs.length) {
    return (
      <EmptyState title="You cannot create agreements">
        Creating an agreement requires the buyer admin role. Ask your organization&apos;s admin.
      </EmptyState>
    );
  }

  async function generate() {
    setBusy(true);
    setError(null);
    try {
      const d = await api<Draft>("/agreements/drafts", { method: "POST", body: { buyer_organization_id: orgId, requirements_text: text } });
      setDraft(d);
      router.replace(`/agreements/new?draft=${d.id}`, { scroll: false });
      if (d.status === "proposed") {
        // Only a short AI summary is offered as a title; long ones would be cut mid-sentence.
        const summary = d.review.service_summary?.trim() ?? "";
        setTitle(summary.length <= 80 ? summary : "");
        const amounts = [...text.matchAll(/\$\s?(\d{1,7}(?:\.\d{1,2})?)/g)].map((m) => m[1]);
        if (amounts.length === 1) setAmount(amounts[0]);
        setStep(1);
      }
    } catch (e) {
      setError(e as ApiError);
    } finally {
      setBusy(false);
    }
  }

  const configIssues = chosen.flatMap((c) => toApiConfig(c, cfg[c.key] ?? defaultCfg(c)).missing.map((m) => `${c.title}: ${m}`));
  const agreementIssues = [
    !title.trim() && "agreement title",
    !merchant && "provider",
    !/^\d{1,7}(\.\d{1,2})?$/.test(amount) && "price",
    !deadline && "delivery deadline",
  ].filter(Boolean) as string[];

  async function createAgreement() {
    if (!draft) return;
    setBusy(true);
    setError(null);
    try {
      const configuration: Cfg = {};
      for (const c of chosen) configuration[c.key] = toApiConfig(c, cfg[c.key] ?? defaultCfg(c)).config;
      const blocking_overrides: Record<string, boolean> = {};
      for (const k of weakened) blocking_overrides[k] = false;
      const res = await api<{ agreement_id: string }>(`/agreements/drafts/${draft.id}/accept`, {
        method: "POST",
        body: {
          merchant_account_id: merchant,
          title: title.trim(),
          amount,
          currency: "USD",
          delivery_deadline: new Date(`${deadline}T23:59:00`).toISOString(),
          include_keys: stated.filter((c) => !excluded.has(c.key)).map((c) => c.key),
          accept_suggestions: [...accepted],
          configuration: Object.fromEntries(Object.entries(configuration)),
          blocking_overrides,
        },
      });
      const a = await api<Agreement>(`/agreements/${res.agreement_id}`);
      setAgreement(a);
      setStep(3);
    } catch (e) {
      setError(e as ApiError);
    } finally {
      setBusy(false);
    }
  }

  const merchantName = merchants.data?.find((m) => m.id === (agreement?.merchant_account_id ?? merchant))?.display_name ?? "—";

  return (
    <div className="mx-auto max-w-4xl space-y-6 animate-fade-in">
      <div>
        <Link href="/dashboard" className="text-sm text-ink-500 hover:text-ink-800">
          ← Dashboard
        </Link>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">New agreement</h1>
      </div>
      <ol className="grid grid-cols-2 gap-2 sm:grid-cols-4" aria-label="Progress">
        {STEPS.map((s, i) => (
          <li
            key={s}
            aria-current={i === step ? "step" : undefined}
            className={cx(
              "rounded-lg border px-3 py-2 text-xs font-medium",
              i === step ? "border-brand-500 bg-brand-50 text-brand-700" : i < step ? "border-pass-100 bg-pass-50 text-pass-700" : "border-ink-200 bg-white text-ink-500",
            )}
          >
            <span className="font-mono">{i + 1}.</span> {s}
          </li>
        ))}
      </ol>

      {error && (
        <Notice tone="fail" title={error.code}>
          {ERROR_HELP[error.code] ?? error.message}
        </Notice>
      )}

      {step === 0 && (
        <Card>
          <CardHeader title="Describe the work" subtitle="Write it the way you would brief a freelancer. Include the price and what success means." />
          <div className="space-y-4 px-4 py-4 sm:px-5">
            {adminOrgs.length > 1 && (
              <Field label="Buyer organization" htmlFor="org">
                <select id="org" className={inputCls} value={orgId} onChange={(e) => setOrgId(e.target.value)}>
                  {adminOrgs.map((o) => (
                    <option key={o.id} value={o.id}>
                      {o.name}
                    </option>
                  ))}
                </select>
              </Field>
            )}
            <Field label="Request" htmlFor="req" hint="AI will propose measurable acceptance criteria. You review every one before anything is created.">
              <textarea id="req" rows={6} maxLength={5000} className={inputCls} value={text} onChange={(e) => setText(e.target.value)} placeholder={EXAMPLE} />
            </Field>
            <div className="flex flex-wrap items-center gap-3">
              <Button onClick={generate} busy={busy} disabled={!text.trim()}>
                {busy ? "Generating criteria…" : "Generate Acceptance Criteria"}
              </Button>
              {!text && (
                <button type="button" className="text-sm font-medium text-brand-700 hover:underline" onClick={() => setText(EXAMPLE)}>
                  Use the example request
                </button>
              )}
            </div>
            {busy && (
              <p role="status" className="flex items-center gap-2 text-sm text-ink-500">
                <Spinner /> The model is reading your request. This usually takes 10–60 seconds.
              </p>
            )}
            {draft?.status === "failed" && (
              <Notice tone="fail" title="AI could not draft criteria">
                <p>{AI_OUTCOME[draft.review.error ?? ""] ?? draft.review.error}</p>
                <p className="mt-1">{draft.review.fallback}</p>
                <div className="mt-3 flex gap-2">
                  <Button size="sm" variant="secondary" onClick={generate} busy={busy}>
                    Retry
                  </Button>
                </div>
              </Notice>
            )}
          </div>
        </Card>
      )}

      {step === 1 && draft && (
        <div className="space-y-6">
          {draft.review.service_summary && (
            <Notice tone="neutral" title="AI reading of your request">
              {draft.review.service_summary}
            </Notice>
          )}
          <Card>
            <CardHeader
              title="Requirements from your request"
              subtitle="Each one quotes your own words. Mandatory wording (“must”) keeps a criterion blocking — the AI cannot weaken it."
            />
            <ul className="divide-y divide-ink-100">
              {stated.map((c) => (
                <CriterionCard
                  key={c.key}
                  c={c}
                  included={!excluded.has(c.key)}
                  onInclude={(v) => setExcluded((s) => toggle(s, c.key, !v))}
                  weakened={weakened.has(c.key)}
                  onWeaken={(v) => setWeakened((s) => toggle(s, c.key, v))}
                />
              ))}
              {!stated.length && <EmptyState title="No verifiable requirement was found in your request" />}
            </ul>
          </Card>

          {suggestions.length > 0 && (
            <Card>
              <CardHeader title="AI suggestions — not in your request" subtitle="Off by default. A suggestion becomes part of the agreement only if you opt in." />
              <ul className="divide-y divide-ink-100">
                {suggestions.map((c) => (
                  <CriterionCard key={c.key} c={c} suggestion included={accepted.has(c.key)} onInclude={(v) => setAccepted((s) => toggle(s, c.key, v))} />
                ))}
              </ul>
            </Card>
          )}

          {(draft.review.ambiguities?.length ?? 0) > 0 && (
            <Card>
              <CardHeader title="Clarification questions" subtitle="Answer these in the configuration step, or edit your request and generate again." />
              <ul className="space-y-3 px-4 py-4 text-sm sm:px-5">
                {draft.review.ambiguities!.map((a, i) => (
                  <li key={i}>
                    <span className="font-medium">{a.topic}: </span>
                    <span className="text-ink-700">{a.question}</span>
                  </li>
                ))}
              </ul>
            </Card>
          )}
          {((draft.review.unsupported_requirements?.length ?? 0) > 0 || (draft.review.rejected_criteria?.length ?? 0) > 0) && (
            <details className="rounded-xl border border-ink-200 bg-white px-4 py-3 text-sm sm:px-5">
              <summary className="cursor-pointer font-medium">Not turned into criteria</summary>
              <ul className="mt-3 space-y-2 text-ink-700">
                {draft.review.unsupported_requirements?.map((u, i) => (
                  <li key={`u${i}`}>
                    <Badge>Unsupported</Badge> {String(u.requirement ?? u.text ?? JSON.stringify(u))} {u.reason ? `— ${String(u.reason)}` : ""}
                  </li>
                ))}
                {draft.review.rejected_criteria?.map((r) => (
                  <li key={r.key}>
                    <Badge tone="fail">Rejected</Badge> {r.title} — {(r.reasons ?? []).join(", ")}
                  </li>
                ))}
              </ul>
            </details>
          )}
          <div className="flex justify-between gap-3">
            <Button variant="secondary" onClick={() => setStep(0)}>
              Back
            </Button>
            <Button onClick={() => setStep(2)} disabled={!chosen.length}>
              Continue to configuration
            </Button>
          </div>
        </div>
      )}

      {step === 2 && draft && (
        <div className="space-y-6">
          <Card>
            <CardHeader title="Agreement terms" />
            <div className="grid gap-4 px-4 py-4 sm:grid-cols-2 sm:px-5">
              <Field label="Title" htmlFor="title">
                <input id="title" className={inputCls} value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Spring campaign landing page" />
              </Field>
              <Field label="Provider" htmlFor="merchant" hint="The provider's PayPal account receives the payment.">
                <select id="merchant" className={inputCls} value={merchant} onChange={(e) => setMerchantId(e.target.value)}>
                  <option value="">Select a provider…</option>
                  {merchants.data?.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.display_name} — {m.organization_name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Price (USD)" htmlFor="amount" hint={amount ? "Taken from your request — check it." : undefined}>
                <input id="amount" inputMode="decimal" className={inputCls} value={amount} onChange={(e) => setAmount(e.target.value.trim())} placeholder="500.00" />
              </Field>
              <Field label="Delivery deadline" htmlFor="deadline">
                <input id="deadline" type="date" className={inputCls} value={deadline} min={new Date().toISOString().slice(0, 10)} onChange={(e) => setDeadline(e.target.value)} />
              </Field>
            </div>
          </Card>
          {chosen.map((c) => (
            <ConfigCard key={c.key} c={c} value={cfg[c.key] ?? defaultCfg(c)} onChange={(v) => setCfg((p) => ({ ...p, [c.key]: v }))} />
          ))}
          {(configIssues.length > 0 || agreementIssues.length > 0) && (
            <Notice tone="review" title="Still needed before the agreement can be created">
              <ul className="list-disc pl-5">
                {agreementIssues.map((i) => (
                  <li key={i}>{i}</li>
                ))}
                {configIssues.map((i) => (
                  <li key={i}>{i}</li>
                ))}
              </ul>
            </Notice>
          )}
          <div className="flex justify-between gap-3">
            <Button variant="secondary" onClick={() => setStep(1)}>
              Back
            </Button>
            <Button onClick={createAgreement} busy={busy} disabled={configIssues.length > 0 || agreementIssues.length > 0}>
              Create draft agreement
            </Button>
          </div>
        </div>
      )}

      {step === 3 && agreement && (
        <div className="space-y-4">
          <Notice tone="info" title="Draft agreement created">
            Nothing is locked and no payment exists yet. Propose the version, then approve it to lock.
          </Notice>
          <LockPanel agreement={agreement} merchantName={merchantName} onChange={setAgreement} />
          <div className="flex justify-end">
            <Link href={`/agreements/${agreement.id}`} className="inline-flex min-h-11 items-center rounded-lg border border-ink-200 bg-white px-4 text-sm font-medium hover:bg-ink-50">
              Open agreement workspace →
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}

function toggle(s: Set<string>, k: string, on: boolean): Set<string> {
  const n = new Set(s);
  if (on) n.add(k);
  else n.delete(k);
  return n;
}

function CriterionCard({
  c,
  included,
  onInclude,
  suggestion,
  weakened,
  onWeaken,
}: {
  c: DraftCriterion;
  included: boolean;
  onInclude: (v: boolean) => void;
  suggestion?: boolean;
  weakened?: boolean;
  onWeaken?: (v: boolean) => void;
}) {
  const id = `inc-${c.key}`;
  const blocking = c.blocking && !weakened;
  return (
    <li className={cx("px-4 py-4 sm:px-5", !included && "bg-ink-50/60")} data-testid={`criterion-${c.key}`}>
      <div className="flex items-start gap-3">
        <input id={id} type="checkbox" className="mt-1 size-5 shrink-0 accent-brand-600" checked={included} onChange={(e) => onInclude(e.target.checked)} />
        <div className="min-w-0 flex-1">
          <label htmlFor={id} className="font-medium text-ink-900">
            {c.title}
          </label>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            <Badge tone={suggestion ? "info" : "neutral"}>{suggestion ? "AI-suggested" : "Buyer-stated"}</Badge>
            {c.kind === "manual_review" ? <Badge tone="review">Manual review</Badge> : <><Badge>{KIND_LABEL[c.kind]}</Badge><Badge>Automated</Badge></>}
            {blocking ? <Badge tone="fail">Blocking</Badge> : <Badge>Non-blocking</Badge>}
          </div>
          <blockquote className="mt-3 border-l-2 border-ink-200 pl-3 text-sm text-ink-600">
            “{c.source_quote}”{suggestion && c.rationale && <span className="mt-1 block text-ink-500">Why suggested: {c.rationale}</span>}
          </blockquote>
          <p className="mt-2 text-sm text-ink-600">{KIND_PLAIN[c.kind]}</p>
          {Object.keys(c.expected).length > 0 && (
            <p className="mt-2 text-sm">
              <span className="text-ink-500">Expected: </span>
              <span className="font-mono text-xs">{Object.entries(c.expected).map(([k, v]) => `${k} = ${String(v)}`).join(", ")}</span>
            </p>
          )}
          {c.needs_configuration.length > 0 && (
            <p className="mt-2 text-sm text-ink-600">
              <span className="text-ink-500">You will need to provide: </span>
              {c.needs_configuration.join("; ")}
            </p>
          )}
          {c.review_notes.length > 0 && (
            <ul className="mt-2 space-y-1 text-xs text-review-800">
              {c.review_notes.map((n) => (
                <li key={n}>⚠ {n}</li>
              ))}
            </ul>
          )}
          {!suggestion && c.blocking && included && onWeaken && (
            <label className="mt-3 flex items-start gap-2 text-sm text-ink-700">
              <input type="checkbox" className="mt-0.5 size-4 accent-fail-700" checked={!!weakened} onChange={(e) => onWeaken(e.target.checked)} />
              <span>
                Make non-blocking <span className="text-ink-500">(your decision, recorded in the audit log — payment would no longer wait for this criterion)</span>
              </span>
            </label>
          )}
          {!suggestion && !included && <p className="mt-2 text-sm text-review-800">Excluded — this requirement from your request will not be verified.</p>}
        </div>
      </div>
    </li>
  );
}

function ConfigCard({ c, value, onChange }: { c: DraftCriterion; value: Record<string, unknown>; onChange: (v: Record<string, unknown>) => void }) {
  const set = (k: string, v: unknown) => onChange({ ...value, [k]: v });
  const fid = (k: string) => `${c.key}-${k}`;
  const pathField = c.kind !== "manual_review" && (
    <Field label="Page path" htmlFor={fid("path")} hint="Relative to the delivery URL the provider submits.">
      <input id={fid("path")} className={inputCls} value={String(value.path ?? "/")} onChange={(e) => set("path", e.target.value)} />
    </Field>
  );
  return (
    <Card>
      <CardHeader title={c.title} subtitle={`${KIND_LABEL[c.kind]} — ${KIND_PLAIN[c.kind]}`} />
      <div className="grid gap-4 px-4 py-4 sm:grid-cols-2 sm:px-5">
        {c.kind === "http_availability" && (
          <>
            {pathField}
            <Field label="Accepted HTTP status codes" htmlFor={fid("st")} hint="Comma-separated, e.g. 200">
              <input id={fid("st")} className={inputCls} value={String(value.expected_status ?? "")} onChange={(e) => set("expected_status", e.target.value)} />
            </Field>
            <label className="flex items-start gap-2 text-sm sm:col-span-2">
              <input type="checkbox" className="mt-0.5 size-4 accent-brand-600" checked={!!value.require_https} onChange={(e) => set("require_https", e.target.checked)} />
              <span>
                Require HTTPS <span className="text-ink-500">— turn off only for local test sites served over plain http.</span>
              </span>
            </label>
          </>
        )}
        {c.kind === "mobile_viewport" && (
          <>
            {pathField}
            <div className="grid grid-cols-2 gap-3">
              <Field label="Viewport width (px)" htmlFor={fid("w")}>
                <input id={fid("w")} type="number" min={280} max={1024} className={inputCls} value={String(value.width)} onChange={(e) => set("width", e.target.value)} />
              </Field>
              <Field label="Height (px)" htmlFor={fid("h")}>
                <input id={fid("h")} type="number" min={480} max={1400} className={inputCls} value={String(value.height)} onChange={(e) => set("height", e.target.value)} />
              </Field>
            </div>
            <Field label="Elements that must be visible (optional)" htmlFor={fid("sel")} hint="CSS selectors, one per line. Leave empty to only check for horizontal overflow.">
              <textarea id={fid("sel")} rows={2} className={inputCls} value={String(value.required_visible_selectors ?? "")} onChange={(e) => set("required_visible_selectors", e.target.value)} />
            </Field>
          </>
        )}
        {c.kind === "contact_form" && <ContactFormConfig value={value} onChange={onChange} fid={fid} pathField={pathField} />}
        {c.kind === "lighthouse_performance" && (
          <>
            {pathField}
            <Field
              label="Minimum performance score (0–100)"
              htmlFor={fid("score")}
              hint={typeof c.expected.min_performance_score === "number" ? "From your request." : "Your request did not state a number — enter the threshold you want."}
            >
              <input id={fid("score")} type="number" min={0} max={100} className={inputCls} value={String(value.min_score ?? "")} onChange={(e) => set("min_score", e.target.value)} />
            </Field>
            <Field label="Device" htmlFor={fid("ff")}>
              <select id={fid("ff")} className={inputCls} value={String(value.form_factor)} onChange={(e) => set("form_factor", e.target.value)}>
                <option value="mobile">Mobile (simulated throttling)</option>
                <option value="desktop">Desktop</option>
              </select>
            </Field>
          </>
        )}
        {c.kind === "manual_review" && (
          <div className="sm:col-span-2">
            <Field label="Instructions for the human reviewer" htmlFor={fid("ins")} hint="Be specific, e.g. which standard (WCAG 2.2 AA) and what to check. Automated performance scores do not establish accessibility.">
              <textarea id={fid("ins")} rows={4} maxLength={2000} className={inputCls} value={String(value.instructions ?? "")} onChange={(e) => set("instructions", e.target.value)} />
            </Field>
          </div>
        )}
      </div>
    </Card>
  );
}

const STANDARD_FORM = {
  form_selector: "form",
  submit_selector: "button[type=submit]",
  success_text: "Thank you",
  fields: [
    { selector: "input[name=name]", value: "OutcomeGuard Verifier" },
    { selector: "input[name=email]", value: "verifier@example.com" },
    { selector: "textarea[name=message]", value: "Automated check" },
  ],
};

function ContactFormConfig({
  value,
  onChange,
  fid,
  pathField,
}: {
  value: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
  fid: (k: string) => string;
  pathField: React.ReactNode;
}) {
  const fields = (value.fields as { selector: string; value: string }[]) ?? [];
  const set = (k: string, v: unknown) => onChange({ ...value, [k]: v });
  const setField = (i: number, k: "selector" | "value", v: string) => set("fields", fields.map((f, j) => (j === i ? { ...f, [k]: v } : f)));
  return (
    <>
      <div className="sm:col-span-2">
        <Notice tone="neutral">
          The verifier fills these fields in a real browser and submits to an OutcomeGuard test endpoint — never to a real inbox. Selectors
          come from the provider&apos;s page; if you don&apos;t know them, you can opt in to the standard field set (name, email, message) and
          the provider builds to it.
          <div className="mt-2">
            <Button size="sm" variant="secondary" type="button" onClick={() => onChange({ ...value, ...STANDARD_FORM })}>
              Use the standard field set
            </Button>
          </div>
        </Notice>
      </div>
      {pathField}
      <Field label="Form selector" htmlFor={fid("form")}>
        <input id={fid("form")} className={inputCls} value={String(value.form_selector ?? "")} onChange={(e) => set("form_selector", e.target.value)} placeholder="e.g. form#contact" />
      </Field>
      <Field label="Submit button selector" htmlFor={fid("sub")}>
        <input id={fid("sub")} className={inputCls} value={String(value.submit_selector ?? "")} onChange={(e) => set("submit_selector", e.target.value)} />
      </Field>
      <Field label="Success message shown after submitting" htmlFor={fid("ok")}>
        <input id={fid("ok")} className={inputCls} value={String(value.success_text ?? "")} onChange={(e) => set("success_text", e.target.value)} />
      </Field>
      <fieldset className="space-y-2 sm:col-span-2">
        <legend className="text-sm font-medium text-ink-800">Fields to fill</legend>
        {fields.map((f, i) => (
          <div key={i} className="grid grid-cols-[1fr_1fr_auto] gap-2">
            <input aria-label={`Field ${i + 1} selector`} className={inputCls} value={f.selector} placeholder="CSS selector" onChange={(e) => setField(i, "selector", e.target.value)} />
            <input aria-label={`Field ${i + 1} test value`} className={inputCls} value={f.value} placeholder="Test value" onChange={(e) => setField(i, "value", e.target.value)} />
            <button type="button" aria-label={`Remove field ${i + 1}`} className="min-h-11 rounded-lg px-3 text-sm text-ink-500 hover:bg-ink-100" onClick={() => set("fields", fields.filter((_, j) => j !== i))}>
              ✕
            </button>
          </div>
        ))}
        {fields.length < 10 && (
          <button type="button" className="text-sm font-medium text-brand-700 hover:underline" onClick={() => set("fields", [...fields, { selector: "", value: "" }])}>
            + Add field
          </button>
        )}
      </fieldset>
    </>
  );
}
