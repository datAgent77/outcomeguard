"use client";

import Link from "next/link";
import type { AgreementSummary } from "@/lib/types";
import { AUTH_STATUS, CAPTURE_STATUS, ORDER_STATUS, POLICY, RUN_STATUS, VERDICT, VERSION_STATUS, type Tone } from "@/lib/status";
import { money, relative } from "@/lib/format";
import { Badge, cx } from "./ui";

export function verificationState(a: AgreementSummary): { label: string; tone: Tone } {
  const r = a.latest_run;
  if (!r) return a.locked ? { label: "Awaiting delivery", tone: "neutral" } : { label: "Not started", tone: "neutral" };
  if (r.status === "queued" || r.status === "running") return RUN_STATUS[r.status];
  if (r.status === "errored") return { label: "Run errored", tone: "fail" };
  const v = VERDICT[r.verdict ?? ""] ?? { label: r.verdict ?? "—", tone: "neutral" as Tone };
  if (r.verdict === "needs_manual_review" && r.awaiting_review === 0) return { label: "Review recorded", tone: "info" };
  return v;
}

export function paymentState(a: AgreementSummary): { label: string; tone: Tone } {
  const p = a.payment;
  if (!p) return { label: "No payment yet", tone: "neutral" };
  if (p.capture_status) return CAPTURE_STATUS[p.capture_status] ?? { label: p.capture_status, tone: "neutral" };
  if (p.authorization_status) return AUTH_STATUS[p.authorization_status] ?? { label: p.authorization_status, tone: "neutral" };
  return ORDER_STATUS[p.order_status] ?? { label: p.order_status, tone: "neutral" };
}

export function AgreementTable({ rows, empty }: { rows: AgreementSummary[]; empty: React.ReactNode }) {
  if (!rows.length) return <>{empty}</>;
  return (
    <>
      <table className="hidden w-full text-left text-sm md:table">
        <thead className="border-b border-ink-100 text-xs uppercase tracking-wider text-ink-500">
          <tr>
            <th className="px-5 py-2.5 font-semibold">Agreement</th>
            <th className="px-3 py-2.5 font-semibold">Amount</th>
            <th className="px-3 py-2.5 font-semibold">Agreement</th>
            <th className="px-3 py-2.5 font-semibold">Verification</th>
            <th className="px-3 py-2.5 font-semibold">Payment</th>
            <th className="px-3 py-2.5 font-semibold">Policy (last evaluated)</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-ink-100">
          {rows.map((a) => {
            const vs = VERSION_STATUS[a.version_status] ?? { label: a.version_status, tone: "neutral" as Tone };
            const ver = verificationState(a);
            const pay = paymentState(a);
            const pol = a.payment?.last_policy_decision ? POLICY[a.payment.last_policy_decision] : null;
            return (
              <tr key={a.id} className="hover:bg-ink-50">
                <td className="max-w-xs px-5 py-3">
                  <Link href={`/agreements/${a.id}`} className="font-medium text-ink-900 hover:text-brand-700">
                    {a.title}
                  </Link>
                  <div className="mt-0.5 truncate text-xs text-ink-500">
                    {a.perspective === "buyer" ? `Provider: ${a.merchant_display_name}` : `Buyer: ${a.buyer_organization_name}`} · v{a.version_number}
                  </div>
                </td>
                <td className="px-3 py-3 font-medium tabular-nums">{money(a.amount, a.currency)}</td>
                <td className="px-3 py-3">
                  <Badge tone={vs.tone}>{vs.label}</Badge>
                </td>
                <td className="px-3 py-3">
                  <Badge tone={ver.tone}>{ver.label}</Badge>
                  {(a.latest_completed_run?.awaiting_review ?? 0) > 0 && (
                    <div className="mt-1 text-xs text-review-800">{a.latest_completed_run!.awaiting_review} awaiting human review</div>
                  )}
                </td>
                <td className="px-3 py-3">
                  <Badge tone={pay.tone}>{pay.label}</Badge>
                </td>
                <td className="px-3 py-3">
                  {pol ? (
                    <>
                      <Badge tone={pol.tone}>{pol.label}</Badge>
                      <div className="mt-1 text-xs text-ink-500">{relative(a.payment?.last_policy_evaluated_at)}</div>
                    </>
                  ) : (
                    <span className="text-xs text-ink-500">Not evaluated</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <ul className="divide-y divide-ink-100 md:hidden">
        {rows.map((a) => {
          const vs = VERSION_STATUS[a.version_status] ?? { label: a.version_status, tone: "neutral" as Tone };
          const ver = verificationState(a);
          const pay = paymentState(a);
          return (
            <li key={a.id}>
              <Link href={`/agreements/${a.id}`} className="block px-4 py-4 active:bg-ink-50">
                <div className="flex items-start justify-between gap-3">
                  <span className="font-medium text-ink-900">{a.title}</span>
                  <span className="shrink-0 font-medium tabular-nums">{money(a.amount, a.currency)}</span>
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  <Badge tone={vs.tone}>{vs.label}</Badge>
                  <Badge tone={ver.tone}>{ver.label}</Badge>
                  <Badge tone={pay.tone}>{pay.label}</Badge>
                </div>
              </Link>
            </li>
          );
        })}
      </ul>
    </>
  );
}

export function StatTile({
  label,
  value,
  tone,
  active,
  onClick,
}: {
  label: string;
  value: number;
  tone: Tone;
  active?: boolean;
  onClick?: () => void;
}) {
  const ring: Record<Tone, string> = {
    neutral: "text-ink-900",
    info: "text-brand-700",
    pass: "text-pass-700",
    review: "text-review-800",
    fail: "text-fail-700",
  };
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cx(
        "rounded-xl border bg-white p-4 text-left transition-colors hover:border-ink-300",
        active ? "border-brand-500 ring-2 ring-brand-100" : "border-ink-200",
      )}
    >
      <div className={cx("text-2xl font-semibold tabular-nums", value ? ring[tone] : "text-ink-500")}>{value}</div>
      <div className="mt-1 text-xs font-medium leading-snug text-ink-600">{label}</div>
    </button>
  );
}
