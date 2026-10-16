// REAL API — complete browser-driven flow against the running local stack:
// web (E2E_BASE_URL) → BFF → OutcomeGuard API → PostgreSQL, the verification worker with real
// Chromium + Lighthouse against the local fixture pages, and the real AI provider for drafting.
//
// Payments: run this against an API in PAYMENT_MODE=rehearsal. Every payment object is then
// SIMULATED and labeled as such in the UI; no PayPal call is made, so no human PayPal approval
// is needed. The real PayPal Sandbox path is exercised manually (docs/P07_UI_VERIFICATION.md).
//
// The manual accessibility review in this test is recorded by the test through the reviewer
// demo account; its rationale says so, and the UI labels it as a scripted demo decision.
import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const PASSWORD = process.env.DEMO_PASSWORD ?? "";
const FIXTURES = process.env.FIXTURES_URL ?? "http://127.0.0.1:8020";
const SHOTS = process.env.E2E_SCREENSHOTS ?? path.resolve(__dirname, "../../../../docs/p07/screenshots");
const REQUEST =
  "Build a mobile-friendly landing page for $500. The contact form must work, the website must be accessible and Lighthouse performance must score at least 90.";
const SCRIPTED =
  "SCRIPTED DEMO DECISION: recorded by the automated P07 browser test through the reviewer demo account. No human performed an accessibility review.";

test.skip(!process.env.E2E_BASE_URL || !PASSWORD, "set E2E_BASE_URL and DEMO_PASSWORD to run against a live local stack");
test.describe.configure({ mode: "serial" });

let agreementUrl = "";
let consoleErrors: string[] = [];

// Runtime errors in the browser console fail the step. (A 4xx the UI handles on purpose,
// e.g. a refused policy action, is logged by Chromium as a failed resource load; those are
// not JavaScript errors and are excluded.)
test.beforeEach(async ({ page }) => {
  consoleErrors = [];
  page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() === "error" && !m.text().startsWith("Failed to load resource")) consoleErrors.push(m.text());
  });
});
test.afterEach(() => {
  expect(consoleErrors, "browser console errors").toEqual([]);
});

async function shot(page: Page, name: string) {
  fs.mkdirSync(SHOTS, { recursive: true });
  await page.evaluate(() => window.scrollTo(0, 0)); // sticky header renders at the top
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true });
}

async function login(page: Page, email: string) {
  await page.context().clearCookies();
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/(dashboard|provider)/);
}

async function submitAndWait(page: Page, url: string) {
  await page.goto(`${agreementUrl}?tab=delivery`);
  const reports = page.getByRole("button", { name: "Open report" });
  await page.waitForLoadState("networkidle");
  const before = await reports.count();
  await page.getByLabel("Delivery URL").fill(url);
  await page.getByRole("button", { name: "Submit evidence for verification" }).click();
  await expect(page.getByText(/Submission accepted|Verification running/).first()).toBeVisible();
  // Real Chromium + Lighthouse run in the worker; the UI polls with a bounded budget.
  await expect(reports).toHaveCount(before + 1, { timeout: 6 * 60_000 });
  await reports.first().click(); // newest run is listed first
  await expect(page.getByText("Verification Report")).toBeVisible();
}

test("1. buyer drafts criteria with real AI, reviews, configures and locks", async ({ page }) => {
  await login(page, "alice@acme.demo");
  await shot(page, "01-buyer-dashboard");
  await page.getByRole("link", { name: "Create Agreement" }).click();
  await page.getByLabel("Request").fill(REQUEST);
  await page.getByRole("button", { name: "Generate Acceptance Criteria" }).click();
  await expect(page.getByText("Requirements from your request")).toBeVisible({ timeout: 180_000 });
  await expect(page.getByText("Buyer-stated").first()).toBeVisible();
  await shot(page, "02-wizard-ai-criteria");
  // Opt in to an availability suggestion if the model proposed one (local fixtures are http).
  const sugg = page.locator('[data-testid^="criterion-"]').filter({ hasText: "AI-suggested" }).filter({ hasText: "HTTP availability" });
  if (await sugg.count()) await sugg.first().getByRole("checkbox").first().check();
  await page.getByRole("button", { name: "Continue to configuration" }).click();

  for (const cb of await page.getByRole("checkbox", { name: /Require HTTPS/ }).all()) await cb.uncheck();
  for (const btn of await page.getByRole("button", { name: "Use the standard field set" }).all()) await btn.click();
  for (const score of await page.getByLabel("Minimum performance score (0–100)").all()) if (!(await score.inputValue())) await score.fill("90");
  for (const ins of await page.getByLabel("Instructions for the human reviewer").all()) if (!(await ins.inputValue())) await ins.fill("Check WCAG 2.2 AA");
  await page.getByLabel("Title").fill("Spring campaign landing page");
  if (!(await page.getByLabel("Price (USD)").inputValue())) await page.getByLabel("Price (USD)").fill("500");
  await page.getByLabel("Delivery deadline").fill(new Date(Date.now() + 14 * 86_400_000).toISOString().slice(0, 10));
  await shot(page, "03-wizard-configure");
  await page.getByRole("button", { name: "Create draft agreement" }).click();
  await expect(page.getByText("Draft agreement created")).toBeVisible();

  await page.getByRole("button", { name: "Propose this version" }).click();
  await page.getByRole("button", { name: "Approve and lock…" }).click();
  await shot(page, "04-lock-confirmation");
  await page.getByRole("dialog").getByRole("button", { name: "Approve and lock" }).click();
  await expect(page.getByText("Locked", { exact: true }).first()).toBeVisible();
  agreementUrl = new URL((await page.getByRole("link", { name: /Open agreement workspace/ }).getAttribute("href"))!, page.url()).pathname;
  expect(agreementUrl).toMatch(/^\/agreements\/[0-9a-f-]{36}$/);
});

test("2. provider submits a broken page → FAIL; cannot review own delivery", async ({ page }) => {
  await login(page, "maya@northwind.demo");
  await shot(page, "05-provider-workspace");
  await submitAndWait(page, `${FIXTURES}/landing-page-broken/`);
  await expect(page.getByRole("heading", { name: "Failed" })).toBeVisible();
  await expect(page.getByTestId(/result-contact/).getByText("FAIL")).toBeVisible();
  const panel = page.getByTestId("manual-review-panel");
  await expect(panel.getByText("Providers cannot review their own delivery")).toBeVisible();
  await expect(panel.getByRole("button", { name: "Record decision…" })).toHaveCount(0);
  await shot(page, "06-evidence-fail");
});

test("3. provider submits the fixed page → NEEDS_MANUAL_REVIEW", async ({ page }) => {
  await login(page, "maya@northwind.demo");
  await submitAndWait(page, `${FIXTURES}/landing-page-fixed/`);
  await expect(page.getByRole("heading", { name: "Needs human review" })).toBeVisible();
  await expect(page.getByTestId(/result-contact/).getByText("PASS")).toBeVisible();
  // Real artifact from the evidence store
  await page.getByRole("button", { name: /Screenshot/ }).first().click();
  await expect(page.getByRole("dialog").getByRole("img")).toBeVisible();
  await shot(page, "07-artifact-screenshot");
  await page.getByRole("dialog").getByRole("button", { name: "Close" }).last().click();
  await shot(page, "08-evidence-needs-review");
});

test("4. buyer authorizes payment (SIMULATED); policy blocks capture pending review", async ({ page, context }) => {
  await login(page, "alice@acme.demo");
  await page.goto(`${agreementUrl}?tab=payment`);
  await page.getByRole("button", { name: "Start PayPal Sandbox checkout" }).click();
  await expect(page.getByText("SIMULATED — rehearsal mode, not PayPal")).toBeVisible();
  const href = await page.getByRole("link", { name: /Open PayPal approval/ }).getAttribute("href");
  // Rehearsal approval page served by the API (stands in for PayPal's buyer page).
  const approve = await context.newPage();
  await approve.goto(href!);
  await approve.getByRole("button", { name: /Approve \(simulated\)/ }).click();
  await approve.close();
  await page.getByRole("button", { name: "Check approval status" }).click();
  await page.getByRole("button", { name: "Authorize payment" }).click();
  await expect(page.getByText("Payment is authorized, not captured. Authorization can expire.")).toBeVisible();
  await expect(page.getByText("Waiting for human review")).toBeVisible();
  await expect(page.getByText("A blocking criterion is waiting for an authorized human review.")).toBeVisible();
  await expect(page.getByRole("button", { name: /Approve capture/ })).toHaveCount(0);
  await shot(page, "09-payment-blocked-review");
});

test("5. reviewer account records the (scripted) accessibility decision", async ({ page }) => {
  await login(page, "rita@acme.demo");
  await page.goto(`${agreementUrl}?tab=evidence`);
  const panel = page.getByTestId("manual-review-panel");
  await expect(panel.getByText("Human Review Required")).toBeVisible();
  await panel.getByLabel("Satisfied", { exact: true }).check();
  await panel.getByLabel(/Rationale/).fill(SCRIPTED);
  await panel.getByLabel(/I personally reviewed/).check();
  await shot(page, "10-manual-review-form");
  await panel.getByRole("button", { name: "Record decision…" }).click();
  await page.getByRole("dialog").getByRole("button", { name: /Record “Satisfied”/ }).click();
  await expect(page.getByText("Scripted demo decision — not a human review")).toBeVisible();
});

test("6. buyer approver approves and captures (SIMULATED); duplicates blocked", async ({ page }) => {
  await login(page, "bob@acme.demo");
  await page.goto(`${agreementUrl}?tab=payment`);
  await expect(page.getByText("Eligible for buyer approval")).toBeVisible();
  await page.getByRole("button", { name: "Approve capture…" }).click();
  await shot(page, "11-capture-approval-dialog");
  await page.getByRole("dialog").getByRole("button", { name: "I approve the capture" }).click();
  await expect(page.getByText("Capture approved by the buyer")).toBeVisible();
  await page.getByRole("button", { name: /Capture \$500\.00 via PayPal/ }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Capture payment" }).click();
  await expect(page.getByText("Payment captured (SIMULATED)")).toBeVisible();
  await expect(page.getByText("ALREADY_CAPTURED")).toBeVisible(); // fresh policy after capture
  await expect(page.getByRole("button", { name: /Capture \$500/ })).toHaveCount(0);
  await shot(page, "12-payment-captured");
});

test("7. ledger shows the persisted trail with honest provenance", async ({ page }) => {
  await login(page, "alice@acme.demo");
  const id = agreementUrl.split("/").pop()!;
  await page.goto(`/ledger?agreement=${id}`);
  const grid = page.locator(".ag-root");
  await expect(grid.getByText("Payment captured (simulated)")).toBeVisible();
  await expect(grid.getByText("Manual review: satisfied")).toBeVisible();
  await expect(grid.getByText("Scripted demo action")).toBeVisible();
  await shot(page, "13-ledger-grid");
  await grid.getByText("Payment captured (simulated)").click();
  await expect(page.getByRole("dialog", { name: "Payment captured (simulated)" })).toBeVisible();
  await shot(page, "14-ledger-detail");
  await page.keyboard.press("Escape");
  await page.goto(`${agreementUrl}?tab=activity`);
  await expect(page.getByText("Agreement approved and locked")).toBeVisible();
  await shot(page, "15-activity-timeline");
});

test("8. mobile layouts of the same real data", async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const page = await ctx.newPage();
  await login(page, "alice@acme.demo");
  await shot(page, "m01-dashboard-390");
  await page.goto(`${agreementUrl}?tab=evidence`);
  await expect(page.getByText("Verification Report")).toBeVisible();
  await shot(page, "m02-evidence-390");
  await page.goto(`${agreementUrl}?tab=payment`);
  await expect(page.getByText("Payment captured (SIMULATED)")).toBeVisible();
  await shot(page, "m03-payment-390");
  const id = agreementUrl.split("/").pop()!;
  await page.goto(`/ledger?agreement=${id}`);
  await expect(page.getByRole("button", { name: "Payment captured (simulated)" })).toBeVisible();
  await shot(page, "m04-ledger-timeline-390");
  const [sw, iw] = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
  expect(sw).toBeLessThanOrEqual(iw);
  await ctx.close();
});
