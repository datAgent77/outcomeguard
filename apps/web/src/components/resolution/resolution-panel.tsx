"use client";

import { useState } from "react";
import { useApi } from "@/lib/client/hooks";
import type { Agreement, CaseSummary, Order, Run } from "@/lib/types";
import { CASE_STATUS, REASON_CATEGORY, REMEDY } from "@/lib/status";
import { dateTime, money } from "@/lib/format";
import { useSession } from "../session";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorState, LoadingBlock, Notice } from "../ui";
import { CaseWorkspace } from "./case-workspace";
import { OpenCase } from "./open-case";

export function ResolutionPanel({
  agreement,
  runs,
  selected,
  onSelect,
  onChanged,
}: {
  agreement: Agreement;
  runs: Run[];
  selected: string | null;
  onSelect: (id: string | null) => void;
  onChanged: () => void;
}) {
  const { user, can } = useSession();
  const cases = useApi<CaseSummary[]>(`/agreements/${agreement.id}/resolutions`);
  const orders = useApi<Order[]>(`/agreements/${agreement.id}/payments`);
  const [opening, setOpening] = useState(false);
  const order = orders.data?.find((o) => o.authorization) ?? orders.data?.[0] ?? null;
  const latestRun = runs.find((r) => r.status === "completed") ?? null;
  const live = cases.data?.find((c) => !["resolved", "closed_no_action", "rejected", "cancelled"].includes(c.status));
  const mayOpen = user.kind === "human" && can(agreement.buyer_organization_id, "buyer_admin", "buyer_approver") && !!agreement.current_locked_version_id && !live;

  if (selected) {
    return (
      <div className="space-y-3">
        <button className="text-sm text-ink-500 hover:text-ink-800" onClick={() => onSelect(null)}>
          ← All cases
        </button>
        <CaseWorkspace
          caseId={selected}
          runs={runs}
          onChanged={() => {
            cases.reload();
            onChanged();
          }}
        />
      </div>
    );
  }
  if (opening) {
    return (
      <OpenCase
        agreement={agreement}
        order={order}
        run={latestRun}
        onCancel={() => setOpening(false)}
        onOpened={(c) => {
          setOpening(false);
          cases.reload();
          onChanged();
          onSelect(c.id);
        }}
      />
    );
  }
  return (
    <div className="space-y-4">
      <Notice tone="neutral" title="How resolution works">
        Either party can raise a problem with a delivered or paid agreement. Opening a case never moves money. A refund or cancellation happens only after the buyer and
        the provider both approve the same proposal and the deterministic policy allows it. This is an internal process between the parties — not a PayPal dispute, and
        OutcomeGuard does not arbitrate or hold funds.
      </Notice>
      <Card>
        <CardHeader
          title="Resolution cases"
          action={
            mayOpen ? (
              <Button size="sm" onClick={() => setOpening(true)}>
                Open a case
              </Button>
            ) : undefined
          }
        />
        <ErrorState error={cases.error} onRetry={cases.reload} />
        {cases.loading && !cases.data ? (
          <LoadingBlock />
        ) : !cases.data?.length ? (
          <EmptyState title="No cases">{mayOpen ? "If something is wrong with the delivery or payment, open a case." : "No problem has been raised for this agreement."}</EmptyState>
        ) : (
          <ul className="divide-y divide-ink-100">
            {cases.data.map((c) => {
              const st = CASE_STATUS[c.status] ?? { label: c.status, tone: "neutral" as const };
              return (
                <li key={c.id}>
                  <button className="flex w-full flex-wrap items-center justify-between gap-2 px-4 py-3 text-left hover:bg-ink-50 sm:px-5" onClick={() => onSelect(c.id)}>
                    <span className="min-w-0">
                      <span className="block font-medium">{REASON_CATEGORY[c.reason_category] ?? c.reason_category}</span>
                      <span className="text-xs text-ink-500">
                        {dateTime(c.created_at)} · requested {REMEDY[c.requested_remedy]}
                        {c.requested_amount && ` ${money(c.requested_amount, c.currency)}`}
                        {c.outcome && ` · ${c.outcome}`}
                      </span>
                    </span>
                    <Badge tone={st.tone}>{st.label}</Badge>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </div>
  );
}
