// MOCKED API — layout checks at the required widths. No horizontal page scroll anywhere.
import { expect, test } from "@playwright/test";
import { AGREEMENT, fakeCookie, MERCHANTS, mockApi, run, summary } from "./fixtures";

const WIDTHS = [1440, 1280, 768, 390, 375];

const ROUTES = {
  "GET /workspace/merchants": MERCHANTS,
  "GET /workspace/agreements": [summary()],
  "GET /agreements/mock-ag-1": AGREEMENT,
  "GET /workspace/agreements/mock-ag-1": summary(),
  "GET /agreements/mock-ag-1/verification-runs": [run("needs_manual_review")],
  "GET /verification/runs/mock-run-fixed/analyses": [],
  "GET /verification/runs/mock-run-fixed/manual-reviews": [],
  "GET /workspace/ledger": {
    total: 1,
    offset: 0,
    limit: 500,
    chain_note: "Rows come from the append-only audit log.",
    rows: [
      { seq: 1, occurred_at: "2026-10-16T09:00:00Z", event_type: "agreement.version.approved_and_locked", category: "agreement", entity_type: "agreement_version", entity_id: "mock-v1", agreement_id: "mock-ag-1", agreement_title: "Landing page", agreement_version: 1, actor: "user:alice@acme.demo", actor_type: "human_account", demo_account: true, provenance: null, verification_run_id: null, criterion_key: null, policy_decision: null, policy_reasons: [], payment_status: null, amount: null, currency: null, evidence_ref: null, transaction_ref: null, data: {}, hash: "a".repeat(64), prev_hash: "0".repeat(64) },
    ],
  },
};

async function noHorizontalScroll(page: import("@playwright/test").Page) {
  const [sw, iw] = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
  expect(sw, "page must not scroll horizontally").toBeLessThanOrEqual(iw);
}

for (const width of WIDTHS) {
  test(`layout at ${width}px`, async ({ page, context, baseURL }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Pay for proof.");
    await noHorizontalScroll(page);

    await fakeCookie(context, baseURL!);
    await mockApi(page, "alice", ROUTES);
    await page.goto("/dashboard");
    await expect(page.getByText("Landing page for spring campaign").filter({ visible: true }).first()).toBeVisible();
    await noHorizontalScroll(page);
    if (width < 768) {
      await expect(page.getByRole("button", { name: "Menu" })).toBeVisible();
      await page.getByRole("button", { name: "Menu" }).click();
      await expect(page.locator("#mobile-nav").getByRole("link", { name: "Ledger" })).toBeVisible();
      const box = await page.locator("#mobile-nav").getByRole("link", { name: "Ledger" }).boundingBox();
      expect(box!.height).toBeGreaterThanOrEqual(44); // touch target
    }

    await page.goto("/agreements/mock-ag-1?tab=evidence");
    await expect(page.getByText("Verification Report")).toBeVisible();
    await noHorizontalScroll(page);

    await page.goto("/ledger");
    if (width < 768) await expect(page.getByRole("button", { name: "Agreement approved and locked" })).toBeVisible();
    else await expect(page.locator(".ag-root")).toBeVisible();
    await noHorizontalScroll(page);
  });
}
