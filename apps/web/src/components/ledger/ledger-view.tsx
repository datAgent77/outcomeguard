"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { useApi } from "@/lib/client/hooks";
import type { AgreementSummary, LedgerPage, LedgerRow } from "@/lib/types";
import { money, dateTime } from "@/lib/format";
import { reasonText } from "@/lib/status";
import { Badge, Button, Card, Dialog, EmptyState, ErrorState, Hash, inputCls, KV, LoadingBlock } from "../ui";
import { actorName, eventTitle, provenance } from "./provenance";
import { Timeline } from "./timeline";

const LedgerGrid = dynamic(() => import("./ledger-grid").then((m) => m.LedgerGrid), {
  ssr: false,
  loading: () => <LoadingBlock rows={8} label="Loading grid" />,
});

const CATEGORIES = [
  ["", "All events"],
  ["agreement", "Agreement"],
  ["ai", "AI"],
  ["verification", "Verification"],
  ["review", "Manual review"],
  ["policy", "Policy"],
  ["approval", "Capture approval"],
  ["payment", "Payment"],
  ["resolution", "Resolution"],
] as const;

const PAGE = 500;

function useIsNarrow(): boolean {
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 767px)");
    const on = () => setNarrow(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return narrow;
}

export function LedgerView() {
  const router = useRouter();
  const params = useSearchParams();
  const agreementId = params.get("agreement") ?? "";
  const category = params.get("category") ?? "";
  const q = params.get("q") ?? "";
  const previews = params.get("previews") === "1";
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState(q);
  const [quick, setQuick] = useState("");
  const [selected, setSelected] = useState<LedgerRow | null>(null);
  const [view, setView] = useState<"table" | "timeline">("table");
  const narrow = useIsNarrow();
  const agreements = useApi<AgreementSummary[]>("/workspace/agreements");

  const qs = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
  if (agreementId) qs.set("agreement_id", agreementId);
  if (category) qs.set("category", category);
  if (q) qs.set("q", q);
  if (previews) qs.set("include_previews", "true");
  const page = useApi<LedgerPage>(`/workspace/ledger?${qs.toString()}`);

  const setParam = (k: string, v: string) => {
    const n = new URLSearchParams(params.toString());
    if (v) n.set(k, v);
    else n.delete(k);
    setOffset(0);
    router.replace(`/ledger?${n.toString()}`, { scroll: false });
  };
  const rows = useMemo(() => page.data?.rows ?? [], [page.data]);
  const showTimeline = narrow || view === "timeline";

  return (
    <div className="space-y-5 animate-fade-in">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Payment &amp; Evidence Ledger</h1>
        <p className="mt-1 max-w-3xl text-sm text-ink-600">Every persisted agreement, verification, review, policy and payment event your organization can see. Click a row for details.</p>
      </div>

      <Card className="p-4">
        <form
          className="grid gap-3 md:grid-cols-[1.4fr_1fr_1fr_auto]"
          onSubmit={(e) => {
            e.preventDefault();
            setParam("q", search.trim());
          }}
        >
          <label className="block">
            <span className="sr-only">Search events</span>
            <input className={inputCls} placeholder="Search event, actor or id (server)…" value={search} onChange={(e) => setSearch(e.target.value)} maxLength={100} />
          </label>
          <label className="block">
            <span className="sr-only">Agreement</span>
            <select className={inputCls} value={agreementId} onChange={(e) => setParam("agreement", e.target.value)}>
              <option value="">All agreements</option>
              {agreements.data?.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.title}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="sr-only">Event category</span>
            <select className={inputCls} value={category} onChange={(e) => setParam("category", e.target.value)}>
              {CATEGORIES.map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </label>
          <Button type="submit" variant="secondary">
            Search
          </Button>
        </form>
        <div className="mt-3 flex flex-wrap items-center gap-4 text-sm">
          <label className="flex items-center gap-2 text-ink-600">
            <input type="checkbox" className="size-4 accent-brand-600" checked={previews} onChange={(e) => setParam("previews", e.target.checked ? "1" : "")} />
            Include policy previews
          </label>
          {!showTimeline && (
            <label className="flex items-center gap-2 text-ink-600">
              Filter loaded rows
              <input className="min-h-9 rounded-lg border border-ink-300 px-2 text-sm" value={quick} onChange={(e) => setQuick(e.target.value)} aria-label="Quick filter loaded rows" />
            </label>
          )}
          <div className="ml-auto hidden gap-1 rounded-lg border border-ink-200 p-0.5 md:flex" role="group" aria-label="View">
            {(["table", "timeline"] as const).map((v) => (
              <button key={v} onClick={() => setView(v)} aria-pressed={view === v} className={`rounded-md px-3 py-1.5 text-sm font-medium ${view === v ? "bg-ink-900 text-white" : "text-ink-600 hover:bg-ink-50"}`}>
                {v === "table" ? "Table" : "Timeline"}
              </button>
            ))}
          </div>
        </div>
      </Card>

      <ErrorState error={page.error} onRetry={page.reload} />
      {page.loading && !page.data ? (
        <LoadingBlock rows={8} />
      ) : rows.length === 0 ? (
        <Card>
          <EmptyState title="No events match">The ledger only shows records that were actually persisted for agreements your organization is party to.</EmptyState>
        </Card>
      ) : showTimeline ? (
        <Card className="px-4 py-5 sm:px-5">
          <Timeline rows={rows} showAgreement onSelect={setSelected} />
        </Card>
      ) : (
        <LedgerGrid rows={rows} quickFilter={quick} onSelect={setSelected} />
      )}

      {page.data && (
        <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-ink-600">
          <span>
            Showing {page.data.total ? offset + 1 : 0}–{offset + rows.length} of {page.data.total}
          </span>
          <div className="flex gap-2">
            <Button size="sm" variant="secondary" disabled={offset === 0} onClick={() => setOffset((o) => Math.max(0, o - PAGE))}>
              Newer
            </Button>
            <Button size="sm" variant="secondary" disabled={offset + rows.length >= page.data.total} onClick={() => setOffset((o) => o + PAGE)}>
              Older
            </Button>
          </div>
        </div>
      )}
      {page.data && <p className="text-xs text-ink-500">{page.data.chain_note}</p>}
      <LedgerDetail row={selected} onClose={() => setSelected(null)} />
    </div>
  );
}

function LedgerDetail({ row, onClose }: { row: LedgerRow | null; onClose: () => void }) {
  if (!row) return null;
  const p = provenance(row);
  return (
    <Dialog open wide onClose={onClose} title={eventTitle(row)}>
      <KV
        items={[
          ["Timestamp", dateTime(row.occurred_at)],
          ["Event type", <code key="e" className="font-mono text-xs">{row.event_type}</code>],
          ["Actor", <span key="a">{actorName(row.actor)} <Badge tone={p.tone}>{p.label}</Badge></span>],
          [
            "Agreement",
            row.agreement_id ? (
              <Link key="ag" href={`/agreements/${row.agreement_id}?tab=activity`} className="text-brand-700 hover:underline">
                {row.agreement_title} {row.agreement_version && `(v${row.agreement_version})`}
              </Link>
            ) : (
              "—"
            ),
          ],
          [
            "Verification run",
            row.verification_run_id && row.agreement_id ? (
              <Link key="r" href={`/agreements/${row.agreement_id}?tab=evidence&run=${row.verification_run_id}`} className="font-mono text-xs text-brand-700 hover:underline">
                {row.verification_run_id} → open evidence
              </Link>
            ) : (
              "—"
            ),
          ],
          ["Criterion", row.criterion_key ?? "—"],
          ["Policy decision", row.policy_decision ? `${row.policy_decision}${row.policy_reasons.length ? ` — ${row.policy_reasons.map(reasonText).join("; ")}` : ""}` : "—"],
          ["Payment status", row.payment_status ?? "—"],
          ["Payment environment", row.payment_environment === "simulated" ? "SIMULATED — rehearsal mode, not PayPal" : row.payment_environment === "sandbox" ? "PayPal Sandbox" : "—"],
          ["Amount", row.amount ? money(row.amount, row.currency) : "—"],
          ["Evidence reference", <Hash key="ev" value={row.evidence_ref} n={16} />],
          ["Transaction reference", row.transaction_ref ? <span key="t" className="font-mono text-xs">{row.transaction_ref}</span> : "—"],
          ["Entity", <span key="en" className="font-mono text-xs">{row.entity_type} · {row.entity_id}</span>],
          ["Audit hash", <Hash key="h" value={row.hash} n={16} />],
          ["Previous hash", <Hash key="ph" value={row.prev_hash} n={16} />],
        ]}
      />
      <p className="mt-4 text-xs font-semibold uppercase tracking-wider text-ink-500">Recorded data (redacted by the API)</p>
      <pre className="mt-1 max-h-64 overflow-auto rounded-lg bg-ink-950 p-3 text-xs text-ink-100">{JSON.stringify(row.data, null, 2)}</pre>
    </Dialog>
  );
}
