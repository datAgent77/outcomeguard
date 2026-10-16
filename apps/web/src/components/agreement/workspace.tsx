"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { use, useCallback, useMemo, useState } from "react";
import { useApi } from "@/lib/client/hooks";
import type { Agreement, AgreementSummary, LedgerPage, Merchant, Run } from "@/lib/types";
import { POLICY, VERSION_STATUS } from "@/lib/status";
import { dateTime, money } from "@/lib/format";
import { paymentState, verificationState } from "../agreement-list";
import { Timeline } from "../ledger/timeline";
import { useSession } from "../session";
import { Badge, Card, CardHeader, EmptyState, ErrorState, Hash, KV, LoadingBlock, Notice, StatusField, Tabs } from "../ui";
import { CriteriaPanel } from "./criteria";
import { DeliveryPanel } from "./delivery";
import { EvidenceReport } from "./evidence-report";
import { LockPanel } from "./lock-panel";
import { PaymentWorkspace } from "./payment";
import { ResolutionPanel } from "../resolution/resolution-panel";
import { ProofPipeline } from "./proof-pipeline";

const TABS = ["overview", "criteria", "delivery", "evidence", "payment", "resolution", "activity"] as const;
type Tab = (typeof TABS)[number];

export function AgreementWorkspace({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams();
  const tab = (TABS as readonly string[]).includes(search.get("tab") ?? "") ? (search.get("tab") as Tab) : "overview";
  const runParam = search.get("run");
  const { isBuyer } = useSession();

  const agreementQ = useApi<Agreement>(`/agreements/${id}`);
  const summaryQ = useApi<AgreementSummary>(`/workspace/agreements/${id}`);
  const runsQ = useApi<Run[]>(`/agreements/${id}/verification-runs`);
  const merchants = useApi<Merchant[]>("/workspace/merchants");
  // A mutation (propose/approve) returns the new agreement; show it until the next reload.
  const [override, setOverride] = useState<{ base: Agreement | undefined; value: Agreement } | null>(null);
  const agreement = override && override.base === agreementQ.data ? override.value : agreementQ.data;
  const setAgreementOverride = (a: Agreement) => setOverride({ base: agreementQ.data, value: a });

  const reloadAll = useCallback(() => {
    agreementQ.reload();
    summaryQ.reload();
    runsQ.reload();
  }, [agreementQ, summaryQ, runsQ]);

  const setTab = (t: Tab, extra?: Record<string, string>) => {
    const q = new URLSearchParams({ tab: t, ...(extra ?? {}) });
    router.replace(`${pathname}?${q.toString()}`, { scroll: false });
  };

  const runs = useMemo(() => runsQ.data ?? [], [runsQ.data]);
  const latestCompleted = runs.find((r) => r.status === "completed") ?? null;
  const selectedRun = (runParam && runs.find((r) => r.id === runParam)) || latestCompleted || runs.find((r) => r.status === "errored") || null;

  if (agreementQ.error) {
    return agreementQ.error.status === 404 ? (
      <EmptyState title="Agreement not found">It does not exist, or your organization is not a party to it.</EmptyState>
    ) : (
      <ErrorState error={agreementQ.error} onRetry={agreementQ.reload} />
    );
  }
  if (!agreement || !summaryQ.data) return <LoadingBlock rows={8} label="Loading agreement" />;

  const s = summaryQ.data;
  const current = agreement.versions.find((v) => v.id === agreement.current_locked_version_id) ?? agreement.versions.reduce((a, b) => (b.version_number > a.version_number ? b : a));
  const vs = VERSION_STATUS[current.status];
  const ver = verificationState(s);
  const pay = paymentState(s);
  const merchant = merchants.data?.find((m) => m.id === agreement.merchant_account_id);

  return (
    <div className="space-y-6 animate-fade-in">
      <div>
        <Link href={isBuyer ? "/dashboard" : "/provider"} className="text-sm text-ink-500 hover:text-ink-800">
          ← {isBuyer ? "Dashboard" : "Provider workspace"}
        </Link>
        <div className="mt-2 flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold tracking-tight">{current.title}</h1>
            <p className="mt-1 text-sm text-ink-600">
              Buyer <strong className="font-medium text-ink-800">{s.buyer_organization_name}</strong> · Provider{" "}
              <strong className="font-medium text-ink-800">{s.merchant_display_name}</strong>
            </p>
          </div>
          <div className="text-right">
            <div className="text-2xl font-semibold tabular-nums">{money(current.amount, current.currency)}</div>
            <div className="text-xs text-ink-500">contract amount · v{current.version_number}</div>
          </div>
        </div>
        <div className="mt-5">
          <ProofPipeline s={s} />
        </div>
        <div className="mt-3 grid grid-cols-1 gap-4 rounded-xl border border-ink-200 bg-white p-4 sm:grid-cols-3">
          <StatusField label="Agreement" tone={vs.tone} value={vs.label} />
          <StatusField label="Verification" tone={ver.tone} value={ver.label} />
          <StatusField label="Payment" tone={pay.tone} value={pay.label} />
        </div>
      </div>

      <Tabs<Tab>
        label="Agreement sections"
        value={tab}
        onChange={(t) => setTab(t)}
        tabs={[
          { id: "overview", label: "Overview" },
          { id: "criteria", label: "Acceptance Criteria", badge: <span className="text-xs text-ink-500">{current.criteria.length}</span> },
          { id: "delivery", label: "Delivery & Verification" },
          { id: "evidence", label: "Evidence", badge: (s.latest_completed_run?.awaiting_review ?? 0) > 0 ? <Badge tone="review">review</Badge> : undefined },
          { id: "payment", label: "Payment" },
          { id: "resolution", label: "Resolution", badge: s.open_resolution ? <Badge tone="review">open</Badge> : undefined },
          { id: "activity", label: "Activity" },
        ]}
      />
      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
        {tab === "overview" && (
          <div className="grid gap-5 lg:grid-cols-[1.4fr_1fr]">
            <div className="space-y-5">
              {current.status !== "locked" && current.status !== "superseded" ? (
                <LockPanel agreement={agreement} merchantName={merchant?.display_name ?? s.merchant_display_name} onChange={(a) => { setAgreementOverride(a); summaryQ.reload(); }} />
              ) : null}
              <Card>
                <CardHeader title="What was promised" />
                <div className="space-y-3 px-4 py-4 text-sm sm:px-5">
                  <blockquote className="border-l-2 border-brand-500 pl-3 text-ink-800">{current.requirements_text}</blockquote>
                  {current.description && <p className="text-ink-600">{current.description}</p>}
                </div>
              </Card>
              <NextStep s={s} onGo={setTab} />
            </div>
            <Card>
              <CardHeader title="Traceability" />
              <div className="px-4 py-4 sm:px-5">
                <KV
                  items={[
                    ["Agreement ID", <Hash key="a" value={agreement.id} />],
                    ["Version", `v${current.version_number} (${current.status})`],
                    ["Content hash", <Hash key="h" value={current.content_hash} n={12} />],
                    ["Locked", dateTime(current.locked_at)],
                    ["Delivery deadline", dateTime(current.delivery_deadline)],
                    ...(current.approvals.length
                      ? ([["Approved by", current.approvals.map((a) => `${a.approver_role.replace("_", " ")} · ${dateTime(a.decided_at)}`).join("; ")]] as [string, string][])
                      : []),
                    ...(s.payment?.last_policy_decision
                      ? ([["Last policy evaluation", `${POLICY[s.payment.last_policy_decision]?.label ?? s.payment.last_policy_decision} · ${dateTime(s.payment.last_policy_evaluated_at)}`]] as [string, string][])
                      : []),
                  ]}
                />
              </div>
            </Card>
          </div>
        )}
        {tab === "criteria" && <CriteriaPanel version={current} />}
        {tab === "delivery" && <DeliveryPanel agreement={agreement} runs={runs} onRunsChange={() => { runsQ.reload(); summaryQ.reload(); }} onOpenRun={(rid) => setTab("evidence", { run: rid })} />}
        {tab === "evidence" &&
          (selectedRun ? (
            <div className="space-y-4">
              {runs.filter((r) => r.status === "completed" || r.status === "errored").length > 1 && (
                <label className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="text-ink-600">Run</span>
                  <select className="min-h-10 rounded-lg border border-ink-300 bg-white px-2 text-sm" value={selectedRun.id} onChange={(e) => setTab("evidence", { run: e.target.value })}>
                    {runs
                      .filter((r) => r.status === "completed" || r.status === "errored")
                      .map((r) => (
                        <option key={r.id} value={r.id}>
                          {dateTime(r.finished_at)} — {r.verdict ?? r.status} {r.id === latestCompleted?.id ? "(latest)" : ""}
                        </option>
                      ))}
                  </select>
                </label>
              )}
              <EvidenceReport
                key={selectedRun.id}
                run={selectedRun}
                version={agreement.versions.find((v) => v.id === selectedRun.agreement_version_id)}
                buyerOrgId={agreement.buyer_organization_id}
                isLatestCompleted={selectedRun.id === latestCompleted?.id}
                onReviewed={reloadAll}
              />
            </div>
          ) : (
            <EmptyState title="No verification report yet">Reports appear here once a submitted delivery has been verified.</EmptyState>
          ))}
        {tab === "payment" && <PaymentWorkspace agreement={agreement} latestRun={s.latest_completed_run} onChange={() => summaryQ.reload()} />}
        {tab === "resolution" && (
          <ResolutionPanel
            agreement={agreement}
            runs={runs}
            selected={search.get("case")}
            onSelect={(cid) => setTab("resolution", cid ? { case: cid } : undefined)}
            onChanged={() => summaryQ.reload()}
          />
        )}
        {tab === "activity" && <Activity agreementId={agreement.id} />}
      </div>
    </div>
  );
}

function NextStep({ s, onGo }: { s: AgreementSummary; onGo: (t: Tab) => void }) {
  const { isBuyer } = useSession();
  const buyer = s.perspective === "buyer";
  let msg: string | null = null;
  let tab: Tab | null = null;
  if (!s.locked) msg = buyer ? "Propose and lock the agreement to start." : "Waiting for the buyer to lock the agreement.";
  else if (!s.latest_run) [msg, tab] = [buyer ? "Waiting for the provider to submit the delivery." : "Submit your delivery for verification.", "delivery"];
  else if (["queued", "running"].includes(s.latest_run.status)) [msg, tab] = ["Verification is running.", "delivery"];
  else if ((s.latest_completed_run?.awaiting_review ?? 0) > 0) [msg, tab] = [buyer ? "A blocking criterion needs a human review." : "Waiting for the buyer's human review.", "evidence"];
  else if (["fail", "inconclusive"].includes(s.latest_completed_run?.verdict ?? "")) [msg, tab] = [buyer ? "Verification failed — the provider needs to fix and resubmit." : "Some requirements failed. Review the report, fix, and resubmit.", "evidence"];
  else if (!s.payment) [msg, tab] = [buyer ? "Start the PayPal Sandbox checkout to authorize the payment." : "Waiting for the buyer to authorize payment.", "payment"];
  else if (s.payment.capture_status === "completed") [msg, tab] = ["Payment captured. See the activity trail.", "activity"];
  else [msg, tab] = [buyer ? "Check the payment policy and approve capture when eligible." : "Payment is authorized, not captured.", "payment"];
  if (!msg) return null;
  return (
    <Notice tone="info" title="Next step">
      {msg}{" "}
      {tab && (
        <button className="font-medium text-brand-700 underline" onClick={() => onGo(tab)}>
          Go there
        </button>
      )}
      {!isBuyer && null}
    </Notice>
  );
}

function Activity({ agreementId }: { agreementId: string }) {
  const [previews, setPreviews] = useState(false);
  const q = useApi<LedgerPage>(`/workspace/ledger?agreement_id=${agreementId}&limit=500${previews ? "&include_previews=true" : ""}`);
  return (
    <Card>
      <CardHeader
        title="Activity"
        subtitle={q.data?.chain_note}
        action={
          <label className="flex items-center gap-2 text-sm text-ink-600">
            <input type="checkbox" className="size-4 accent-brand-600" checked={previews} onChange={(e) => setPreviews(e.target.checked)} />
            Include policy previews
          </label>
        }
      />
      <div className="px-4 py-5 sm:px-5">
        <ErrorState error={q.error} onRetry={q.reload} />
        {q.loading && !q.data ? <LoadingBlock /> : q.data && q.data.rows.length === 0 ? <EmptyState title="No events yet" /> : q.data && <Timeline rows={[...q.data.rows].reverse()} />}
      </div>
    </Card>
  );
}
