"use client";

import type { AgreementSummary } from "@/lib/types";
import { cx } from "../ui";

type State = "done" | "current" | "blocked" | "todo";

/** The story of one agreement in five steps — read from real state, nothing inferred
 *  beyond what the API reports (policy shown is the last stored evaluation). */
export function steps(s: AgreementSummary): { label: string; detail: string; state: State; who: string }[] {
  const run = s.latest_completed_run;
  const pay = s.payment;
  const verified = run && (run.verdict === "pass" || (run.verdict === "needs_manual_review" && run.awaiting_review === 0));
  const failed = run && (run.verdict === "fail" || run.verdict === "inconclusive");
  const captured = pay?.capture_status === "completed";
  const reviewNeeded = run?.verdict === "needs_manual_review";
  return [
    {
      label: "Promise locked",
      who: "Buyer",
      detail: s.locked ? `v${s.version_number} · criteria frozen` : "criteria not locked yet",
      state: s.locked ? "done" : "current",
    },
    {
      label: "Delivery verified",
      who: "Chromium + Lighthouse",
      detail: !s.locked ? "after locking" : failed ? "a blocking check failed" : run ? (run.verdict === "pass" ? "all checks passed" : "automated checks passed") : s.latest_run ? "verification running" : "waiting for delivery",
      state: !s.locked ? "todo" : failed ? "blocked" : run ? "done" : "current",
    },
    {
      label: "Human review",
      who: "Authorized reviewer",
      detail: !run ? "if required" : !reviewNeeded ? "not required" : run.awaiting_review ? "waiting for reviewer" : "decision recorded",
      state: !run || failed ? "todo" : !reviewNeeded || !run.awaiting_review ? "done" : "current",
    },
    {
      label: "Buyer approves",
      who: "Buyer approver",
      detail: captured ? "capture approved" : pay?.last_policy_decision === "eligible_for_approval" ? "policy: eligible" : pay?.authorization_status === "authorized" ? "policy decides" : pay ? "authorize first" : "PayPal checkout first",
      state: captured ? "done" : verified && pay?.authorization_status === "authorized" ? "current" : "todo",
    },
    {
      label: "PayPal capture",
      who: !pay ? "PayPal" : pay.environment === "simulated" ? "SIMULATED (rehearsal)" : "PayPal Sandbox",
      detail: captured ? (pay?.refunded ? `captured · ${pay.refunded} refunded` : "captured") : pay?.authorization_status === "voided" ? "authorization cancelled" : pay?.authorization_status ? "authorized, not captured" : "not started",
      state: captured ? "done" : pay?.authorization_status === "voided" ? "blocked" : "todo",
    },
  ];
}

const DOT: Record<State, string> = {
  done: "bg-pass-600 text-white",
  current: "bg-brand-600 text-white ring-4 ring-brand-100",
  blocked: "bg-fail-600 text-white",
  todo: "bg-ink-200 text-ink-500",
};

export function ProofPipeline({ s }: { s: AgreementSummary }) {
  return (
    <ol className="grid grid-cols-1 gap-3 rounded-xl border border-ink-200 bg-white p-4 sm:grid-cols-5" aria-label="Proof pipeline" data-testid="proof-pipeline">
      {steps(s).map((st, i) => (
        <li key={st.label} className="flex items-start gap-3 sm:flex-col sm:gap-2">
          <span className={cx("grid size-7 shrink-0 place-items-center rounded-full text-xs font-semibold", DOT[st.state])} aria-hidden>
            {st.state === "done" ? "✓" : st.state === "blocked" ? "!" : i + 1}
          </span>
          <span className="min-w-0">
            <span className="block text-sm font-semibold text-ink-900">
              {st.label}
              <span className="sr-only"> — {st.state}</span>
            </span>
            <span className="block text-xs text-ink-600">{st.detail}</span>
            <span className="block text-[11px] uppercase tracking-wider text-ink-500">{st.who}</span>
          </span>
        </li>
      ))}
    </ol>
  );
}
