"use client";

import Link from "next/link";
import type { LedgerRow } from "@/lib/types";
import { money, dateTime } from "@/lib/format";
import { reasonText } from "@/lib/status";
import { Badge, cx, Hash } from "../ui";
import { actorName, eventTitle, eventTone, provenance } from "./provenance";

const DOT = { neutral: "bg-ink-300", info: "bg-brand-500", pass: "bg-pass-600", review: "bg-review-700", fail: "bg-fail-600" };

export function Timeline({ rows, showAgreement, onSelect }: { rows: LedgerRow[]; showAgreement?: boolean; onSelect?: (r: LedgerRow) => void }) {
  return (
    <ol className="relative space-y-0">
      {rows.map((r, i) => {
        const p = provenance(r);
        const tone = eventTone(r);
        return (
          <li key={r.seq} className="relative flex gap-3 pb-5">
            {i < rows.length - 1 && <span className="absolute left-[7px] top-4 h-full w-px bg-ink-200" aria-hidden />}
            <span className={cx("relative mt-1.5 size-[15px] shrink-0 rounded-full border-[3px] border-white ring-1 ring-ink-200", DOT[tone])} aria-hidden />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                {onSelect ? (
                  <button className="text-left text-sm font-medium text-ink-900 hover:text-brand-700" onClick={() => onSelect(r)}>
                    {eventTitle(r)}
                  </button>
                ) : (
                  <span className="text-sm font-medium text-ink-900">{eventTitle(r)}</span>
                )}
                <time className="text-xs text-ink-500" dateTime={r.occurred_at}>
                  {dateTime(r.occurred_at)}
                </time>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-ink-600">
                <Badge tone={p.tone}>{p.label}</Badge>
                <span className="break-all">{actorName(r.actor)}</span>
                {showAgreement && r.agreement_id && (
                  <Link href={`/agreements/${r.agreement_id}?tab=activity`} className="text-brand-700 hover:underline">
                    {r.agreement_title}
                  </Link>
                )}
              </div>
              <div className="mt-1 space-y-0.5 text-xs text-ink-600">
                {r.criterion_key && <div>Criterion: {r.criterion_key}</div>}
                {r.policy_reasons.length > 0 && <div>{r.policy_reasons.map(reasonText).join(" · ")}</div>}
                {r.amount && (
                  <div>
                    {money(r.amount, r.currency)}
                    {r.payment_status && ` · ${r.payment_status}`}
                    {r.payment_environment === "simulated" && <strong className="ml-1 text-review-800">· SIMULATED (rehearsal)</strong>}
                  </div>
                )}
                {r.transaction_ref && <div>PayPal ref <span className="font-mono">{r.transaction_ref}</span></div>}
                {r.verification_run_id && r.agreement_id && (
                  <div>
                    Run{" "}
                    <Link href={`/agreements/${r.agreement_id}?tab=evidence&run=${r.verification_run_id}`} className="font-mono text-brand-700 hover:underline">
                      {r.verification_run_id.slice(0, 8)}…
                    </Link>
                  </div>
                )}
                {r.evidence_ref && (
                  <div>
                    Evidence ref <Hash value={r.evidence_ref} />
                  </div>
                )}
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
