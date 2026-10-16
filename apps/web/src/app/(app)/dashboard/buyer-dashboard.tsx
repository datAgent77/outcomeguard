"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { AgreementTable, StatTile } from "@/components/agreement-list";
import { useSession } from "@/components/session";
import { Card, CardHeader, EmptyState, ErrorState, LoadingBlock } from "@/components/ui";
import { useApi } from "@/lib/client/hooks";
import type { AgreementSummary } from "@/lib/types";
import type { Tone } from "@/lib/status";

type Bucket = { id: string; label: string; tone: Tone; test: (a: AgreementSummary) => boolean };

const BUCKETS: Bucket[] = [
  { id: "approval", label: "Agreements awaiting approval", tone: "review", test: (a) => a.version_status === "proposed" || a.version_status === "draft" },
  {
    id: "verification",
    label: "Deliveries awaiting verification",
    tone: "info",
    test: (a) => a.locked && (!a.latest_run || a.latest_run.status === "queued" || a.latest_run.status === "running"),
  },
  {
    id: "failed",
    label: "Failed verifications",
    tone: "fail",
    test: (a) => a.latest_run?.status === "errored" || ["fail", "inconclusive"].includes(a.latest_completed_run?.verdict ?? ""),
  },
  { id: "review", label: "Outstanding manual reviews", tone: "review", test: (a) => (a.latest_completed_run?.awaiting_review ?? 0) > 0 },
  {
    id: "eligible",
    label: "Eligible for capture approval",
    tone: "pass",
    test: (a) => a.payment?.last_policy_decision === "eligible_for_approval" && !a.payment.capture_status,
  },
  { id: "blocked", label: "Payments blocked by policy", tone: "fail", test: (a) => a.payment?.last_policy_decision === "blocked" && a.payment.capture_status !== "completed" },
  { id: "completed", label: "Completed payments", tone: "pass", test: (a) => a.payment?.capture_status === "completed" },
];

export function BuyerDashboard() {
  const { user, buyerOrgs } = useSession();
  const q = useApi<AgreementSummary[]>("/workspace/agreements");
  const [filter, setFilter] = useState<string | null>(null);
  const rows = useMemo(() => (q.data ?? []).filter((a) => a.perspective === "buyer"), [q.data]);
  const shown = filter ? rows.filter(BUCKETS.find((b) => b.id === filter)!.test) : rows;
  const canCreate = buyerOrgs.some((o) => o.roles.includes("buyer_admin"));

  if (!buyerOrgs.length) {
    return <EmptyState title="No buyer organization">Your account is not a member of a buyer organization.</EmptyState>;
  }
  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm text-ink-500">{buyerOrgs.map((o) => o.name).join(", ")}</p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">Welcome back, {user.display_name.split(" (")[0]}</h1>
        </div>
        {canCreate && (
          <Link href="/agreements/new" className="inline-flex min-h-11 items-center rounded-lg bg-brand-600 px-4 text-sm font-semibold text-white shadow-sm hover:bg-brand-700">
            Create Agreement
          </Link>
        )}
      </div>

      <ErrorState error={q.error} onRetry={q.reload} />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
        {BUCKETS.map((b) => (
          <StatTile
            key={b.id}
            label={b.label}
            value={q.data ? rows.filter(b.test).length : 0}
            tone={b.tone}
            active={filter === b.id}
            onClick={() => setFilter((f) => (f === b.id ? null : b.id))}
          />
        ))}
      </div>
      <p className="-mt-3 text-xs text-ink-500">
        Counts come from your organization&apos;s records. Payment eligibility reflects the <em>last stored</em> policy evaluation;
        the payment tab re-evaluates before any approval or capture.
      </p>

      <Card>
        <CardHeader
          title={filter ? BUCKETS.find((b) => b.id === filter)!.label : "All agreements"}
          subtitle={`${shown.length} of ${rows.length}`}
          action={
            filter ? (
              <button className="text-sm font-medium text-brand-700 hover:underline" onClick={() => setFilter(null)}>
                Clear filter
              </button>
            ) : undefined
          }
        />
        {q.loading && !q.data ? (
          <LoadingBlock rows={4} />
        ) : (
          <AgreementTable
            rows={shown}
            empty={
              <EmptyState
                title={filter ? "Nothing here" : "No agreements yet"}
                action={
                  !filter && canCreate ? (
                    <Link href="/agreements/new" className="text-sm font-semibold text-brand-700 hover:underline">
                      Create your first agreement →
                    </Link>
                  ) : undefined
                }
              >
                {filter ? "No agreement is in this state right now." : "Describe the work, agree on measurable criteria, and pay only for verified delivery."}
              </EmptyState>
            }
          />
        )}
      </Card>
    </div>
  );
}
