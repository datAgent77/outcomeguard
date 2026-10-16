// MOCKED API — automated accessibility scan (axe-core, WCAG 2.x A/AA rules) of key screens.
// Automated scans find only part of the issues; this is not a WCAG conformance claim.
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { AGREEMENT, fakeCookie, MERCHANTS, mockApi, order, POLICY_ELIGIBLE, run, summary } from "./fixtures";

async function scan(page: Page) {
  await page.waitForTimeout(400); // let the 180 ms fade-in finish; partial opacity skews contrast
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).exclude(".ag-root-wrapper").analyze();
  const serious = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(serious.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(", ")}`)).toEqual([]);
}

test("landing and login", async ({ page }) => {
  await page.goto("/");
  await scan(page);
  await page.goto("/login");
  await scan(page);
});

test("dashboard, evidence report and payment workspace", async ({ page, context, baseURL }) => {
  await fakeCookie(context, baseURL!);
  await mockApi(page, "rita", {
    "GET /workspace/merchants": MERCHANTS,
    "GET /workspace/agreements": [summary()],
    "GET /agreements/mock-ag-1": AGREEMENT,
    "GET /workspace/agreements/mock-ag-1": summary(),
    "GET /agreements/mock-ag-1/verification-runs": [run("needs_manual_review")],
    "GET /verification/runs/mock-run-fixed/analyses": [],
    "GET /verification/runs/mock-run-fixed/manual-reviews": [],
    "GET /agreements/mock-ag-1/payments": [order()],
    "GET /payments/authorizations/mock-auth/policy": POLICY_ELIGIBLE,
    "GET /payments/authorizations/mock-auth/capture-approvals": [],
  });
  await page.goto("/dashboard");
  await expect(page.getByText("Landing page for spring campaign").filter({ visible: true })).toBeVisible();
  await scan(page);
  await page.goto("/agreements/mock-ag-1?tab=evidence");
  await expect(page.getByTestId("manual-review-panel")).toBeVisible();
  await scan(page);
  await page.goto("/agreements/mock-ag-1?tab=payment");
  await expect(page.getByText("Eligible for buyer approval")).toBeVisible();
  await scan(page);
});
