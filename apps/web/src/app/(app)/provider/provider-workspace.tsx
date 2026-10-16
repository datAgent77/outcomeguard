"use client";

import { useMemo, useState } from "react";
import { AgreementTable, StatTile } from "@/components/agreement-list";
import { useSession } from "@/components/session";
import { Card, CardHeader, EmptyState, ErrorState, LoadingBlock, Notice } from "@/components/ui";
import { useApi } from "@/lib/client/hooks";
import type { AgreementSummary } from "@/lib/types";
import type { Tone } from "@/lib/status";

const BUCKETS: { id: string; label: string; tone: Tone; test: (a: AgreementSummary) => boolean }[] = [
  { id: "deliver", label: "Locked — ready for delivery", tone: "info", test: (a) => a.locked && !a.latest_run },
  { id: "running", label: "Verification in progress", tone: "info", test: (a) => ["queued", "running"].includes(a.latest_run?.status ?? "") },
  {
    id: "fix",
    label: "Failed requirements to fix",
    tone: "fail",
    test: (a) => a.latest_run?.status === "errored" || ["fail", "inconclusive"].includes(a.latest_completed_run?.verdict ?? ""),
  },
  { id: "review", label: "Waiting for buyer review", tone: "review", test: (a) => (a.latest_completed_run?.awaiting_review ?? 0) > 0 },
  { id: "authorized", label: "Payment authorized (not captured)", tone: "info", test: (a) => !!a.payment?.authorization_status && !a.payment.capture_status },
  { id: "paid", label: "Payment captured", tone: "pass", test: (a) => a.payment?.capture_status === "completed" },
  { id: "cases", label: "Open resolution cases", tone: "review", test: (a) => !!a.open_resolution },
];

export function ProviderWorkspace() {
  const { merchantOrgs } = useSession();
  const q = useApi<AgreementSummary[]>("/workspace/agreements");
  const [filter, setFilter] = useState<string | null>(null);
  const rows = useMemo(() => (q.data ?? []).filter((a) => a.perspective === "provider"), [q.data]);
  const shown = filter ? rows.filter(BUCKETS.find((b) => b.id === filter)!.test) : rows;
  if (!merchantOrgs.length) {
    return <EmptyState title="No provider organization">Your account is not a member of a provider organization.</EmptyState>;
  }
  return (
    <div className="space-y-6 animate-fade-in">
      <div>
        <p className="text-sm text-ink-500">{merchantOrgs.map((o) => o.name).join(", ")}</p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight">Provider workspace</h1>
        <p className="mt-1 max-w-2xl text-sm text-ink-600">
          Agreements assigned to your organization. Submit deliveries and see exactly which requirements passed or failed. Buyers
          decide manual reviews and approve capture — you cannot do either for them.
        </p>
      </div>
      <ErrorState error={q.error} onRetry={q.reload} />
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-7">
        {BUCKETS.map((b) => (
          <StatTile key={b.id} label={b.label} value={q.data ? rows.filter(b.test).length : 0} tone={b.tone} active={filter === b.id} onClick={() => setFilter((f) => (f === b.id ? null : b.id))} />
        ))}
      </div>
      <Card>
        <CardHeader title={filter ? BUCKETS.find((b) => b.id === filter)!.label : "Assigned agreements"} subtitle={`${shown.length} of ${rows.length}`} />
        {q.loading && !q.data ? (
          <LoadingBlock rows={4} />
        ) : (
          <AgreementTable rows={shown} empty={<EmptyState title={filter ? "Nothing here" : "No agreements assigned yet"}>Agreements appear here once a buyer creates one with your organization as the provider.</EmptyState>} />
        )}
      </Card>
      <Notice tone="neutral" title="Acceptance criteria are fixed once locked">
        A locked agreement version cannot be edited. Changes require the buyer to create and lock a new version.
      </Notice>
    </div>
  );
}
