// MOCKED API — AG Grid ledger: rows only from the API, filters go to the server, details drawer.
import { expect, test } from "@playwright/test";
import { fakeCookie, mockApi, summary } from "./fixtures";

const row = (seq: number, over: Record<string, unknown>) => ({
  seq,
  occurred_at: `2026-10-16T09:0${seq}:00Z`,
  event_type: "evidence.submitted",
  category: "verification",
  entity_type: "evidence_submission",
  entity_id: `mock-e-${seq}`,
  agreement_id: "mock-ag-1",
  agreement_title: "Landing page",
  agreement_version: 1,
  actor: "user:maya@northwind.demo",
  actor_type: "human_account",
  demo_account: true,
  provenance: null,
  verification_run_id: null,
  criterion_key: null,
  policy_decision: null,
  policy_reasons: [],
  payment_status: null,
  amount: null,
  currency: null,
  evidence_ref: null,
  transaction_ref: null,
  data: {},
  hash: String(seq).repeat(64).slice(0, 64),
  prev_hash: "0".repeat(64),
  ...over,
});

test("ledger grid renders API rows, server filters and a detail drawer", async ({ page, context, baseURL }) => {
  await fakeCookie(context, baseURL!);
  const queries: string[] = [];
  await mockApi(page, "alice", {
    "GET /workspace/agreements": [summary()],
    "GET /workspace/ledger": (_r, url) => {
      queries.push(url.search);
      return {
        total: 2,
        offset: 0,
        limit: 500,
        chain_note: "Rows come from the append-only audit log. … not tamper-proof against someone with database write access.",
        rows: [
          row(2, { event_type: "manual_review.satisfied", category: "review", criterion_key: "website_accessible", provenance: "scripted", actor: "user:rita@acme.demo" }),
          row(1, { event_type: "payment.capture.completed", category: "payment", amount: "500.00", currency: "USD", transaction_ref: "MOCKCAP789", payment_status: "capture completed", actor: "system" , actor_type: "system", demo_account: false}),
        ],
      };
    },
  });
  await page.goto("/ledger");
  const grid = page.locator(".ag-root");
  await expect(grid.getByText("Manual review: satisfied")).toBeVisible();
  await expect(grid.getByText("Scripted demo action")).toBeVisible();
  await expect(page.getByText("not tamper-proof")).toBeVisible();

  await page.getByLabel("Event category").selectOption("payment");
  await expect.poll(() => queries.at(-1)).toContain("category=payment");
  await page.getByPlaceholder(/Search event/).fill("rita");
  await page.getByRole("button", { name: "Search" }).click();
  await expect.poll(() => queries.at(-1)).toContain("q=rita");

  await grid.getByText("Payment captured").click();
  const dlg = page.getByRole("dialog", { name: "Payment captured" });
  await expect(dlg.getByText("MOCKCAP789")).toBeVisible();
  await expect(dlg.getByText("System", { exact: true })).toBeVisible();
});
