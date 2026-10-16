"use client";

import { AllCommunityModule, ModuleRegistry, themeQuartz, type ColDef, type ValueFormatterParams } from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import { useMemo } from "react";
import type { LedgerRow } from "@/lib/types";
import { dateTime } from "@/lib/format";
import { actorName, eventTitle, provenance } from "./provenance";

// AG Grid Community only — no Enterprise modules, no licence key.
ModuleRegistry.registerModules([AllCommunityModule]);

const theme = themeQuartz.withParams({
  accentColor: "#2952d4",
  foregroundColor: "#0f1f3a",
  borderColor: "#dde2ea",
  headerBackgroundColor: "#f6f8fb",
  fontFamily: "inherit",
  fontSize: 13,
  headerFontWeight: 600,
  rowHoverColor: "#f2f5fe",
  wrapperBorderRadius: 12,
});

const short = (p: ValueFormatterParams<LedgerRow, string | null>) => (p.value ? `${p.value.slice(0, 10)}…` : "");

export function LedgerGrid({ rows, quickFilter, onSelect }: { rows: LedgerRow[]; quickFilter: string; onSelect: (r: LedgerRow) => void }) {
  const columns = useMemo<ColDef<LedgerRow>[]>(
    () => [
      { field: "occurred_at", headerName: "Timestamp", sort: "desc", width: 190, valueFormatter: (p) => dateTime(p.value), filter: "agDateColumnFilter", filterValueGetter: (p) => (p.data ? new Date(p.data.occurred_at) : null) },
      { colId: "event", headerName: "Event", width: 260, valueGetter: (p) => (p.data ? eventTitle(p.data) : ""), filter: true },
      { field: "agreement_title", headerName: "Agreement", width: 200, filter: true },
      { field: "agreement_version", headerName: "Ver.", width: 80, valueFormatter: (p) => (p.value ? `v${p.value}` : "") },
      { field: "actor", headerName: "Actor", width: 200, valueFormatter: (p) => actorName(p.value ?? ""), filter: true },
      { colId: "actor_type", headerName: "Actor type", width: 170, valueGetter: (p) => (p.data ? provenance(p.data).label : ""), filter: true },
      { field: "verification_run_id", headerName: "Run", width: 130, valueFormatter: short },
      { field: "criterion_key", headerName: "Criterion", width: 170, filter: true },
      { field: "policy_decision", headerName: "Policy decision", width: 170, filter: true },
      { field: "payment_status", headerName: "Payment status", width: 190, filter: true },
      { field: "payment_environment", headerName: "Payment env.", width: 140, valueFormatter: (p) => (p.value === "simulated" ? "SIMULATED" : p.value === "sandbox" ? "PayPal Sandbox" : ""), filter: true },
      { field: "amount", headerName: "Amount", width: 110, type: "rightAligned" },
      { field: "currency", headerName: "Cur.", width: 80 },
      { field: "evidence_ref", headerName: "Evidence ref", width: 140, valueFormatter: short },
      { field: "transaction_ref", headerName: "Transaction ref", width: 190 },
    ],
    [],
  );
  return (
    <div style={{ height: 620 }}>
      <AgGridReact<LedgerRow>
        theme={theme}
        rowData={rows}
        columnDefs={columns}
        defaultColDef={{ sortable: true, resizable: true }}
        getRowId={(p) => String(p.data.seq)}
        quickFilterText={quickFilter}
        pagination
        paginationPageSize={25}
        paginationPageSizeSelector={[25, 50, 100]}
        onRowClicked={(e) => e.data && onSelect(e.data)}
        rowSelection={{ mode: "singleRow", checkboxes: false, enableClickSelection: true }}
        enableCellTextSelection
        ensureDomOrder
      />
    </div>
  );
}
