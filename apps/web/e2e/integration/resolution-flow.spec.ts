// REAL API, SIMULATED payments (API in PAYMENT_MODE=rehearsal) — P08 resolution flow.
// Setup (agreement, verification, scripted review, checkout/authorize/capture) goes through
// the real BFF + API as JSON calls; every P08 step (open case, propose, approve, refund,
// cancel authorization, ledger) is driven through the UI. No PayPal call is made.
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const PASSWORD = process.env.DEMO_PASSWORD ?? "";
const FIXTURES = process.env.FIXTURES_URL ?? "http://127.0.0.1:8020";
const SHOTS = process.env.E2E_SCREENSHOTS ?? path.resolve(__dirname, "../../../../docs/p08/screenshots");

test.skip(!process.env.E2E_BASE_URL || !PASSWORD, "set E2E_BASE_URL and DEMO_PASSWORD to run against a live local stack");
test.describe.configure({ mode: "serial" });

let consoleErrors: string[] = [];
test.beforeEach(async ({ page }) => {
  consoleErrors = [];
  page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() === "error" && !m.text().startsWith("Failed to load resource")) consoleErrors.push(m.text());
  });
});
test.afterEach(() => expect(consoleErrors, "browser console errors").toEqual([]));

async function shot(page: Page, name: string) {
  fs.mkdirSync(SHOTS, { recursive: true });
  await page.evaluate(() => window.scrollTo(0, 0));
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

function bff(page: Page, baseURL: string) {
  const req: APIRequestContext = page.request;
  const headers = { "x-og-csrf": "1", origin: baseURL, "content-type": "application/json" };
  return {
    get: async (p: string) => {
      const r = await req.get(`/api/og${p}`);
      expect(r.ok(), `${p}: ${r.status()}`).toBeTruthy();
      return r.json();
    },
    post: async (p: string, data?: unknown, extra: Record<string, string> = {}) => {
      const r = await req.post(`/api/og${p}`, { data: data ?? {}, headers: { ...headers, ...extra } });
      expect(r.ok(), `${p}: ${r.status()} ${await r.text()}`).toBeTruthy();
      return r.json();
    },
  };
}

const CRITERIA = [
  { key: "site_available", title: "Landing page is reachable", spec: { kind: "http_availability", require_https: false } },
  { key: "mobile_layout", title: "Fits a 390 px phone viewport", spec: { kind: "mobile_viewport" } },
  {
    key: "contact_form_works",
    title: "Contact form can be submitted",
    spec: {
      kind: "contact_form",
      fields: [
        { selector: "input[name=name]", value: "OutcomeGuard Verifier" },
        { selector: "input[name=email]", value: "verifier@example.com" },
        { selector: "textarea[name=message]", value: "Automated check" },
      ],
      success_text: "Thank you",
    },
  },
  { key: "lighthouse_performance", title: "Lighthouse performance ≥ 90", spec: { kind: "lighthouse_performance", min_score: 90 } },
  { key: "website_accessible", title: "Website is accessible (human review)", spec: { kind: "manual_review", instructions: "Check WCAG 2.2 AA" } },
];

/** Locked agreement + authorized SIMULATED payment, via the real API. */
async function lockedAndAuthorized(page: Page, baseURL: string, title: string) {
  await login(page, "alice@acme.demo");
  const api = bff(page, baseURL);
  const me = await api.get("/workspace/context");
  const merchants = await api.get("/workspace/merchants");
  const a = await api.post("/agreements", {
    buyer_organization_id: me.organizations[0].id,
    merchant_account_id: merchants[0].id,
    title,
    requirements_text: "Build a mobile-friendly landing page for $500. The contact form must work and the website must be accessible.",
    amount: "500.00",
    delivery_deadline: new Date(Date.now() + 14 * 86_400_000).toISOString(),
    criteria: CRITERIA,
  });
  const v = await api.post(`/agreements/${a.id}/versions/1/propose`, { expected_revision: a.versions[0].revision });
  await api.post(`/agreements/${a.id}/versions/1/approve`, { content_hash: v.versions[0].content_hash });
  const order = await api.post(`/agreements/${a.id}/payments`, {}, { "idempotency-key": `pay-${Date.now()}` });
  // Rehearsal approval page (stands in for PayPal's buyer page).
  const approve = await page.context().newPage();
  await approve.goto(order.approval_url);
  await approve.getByRole("button", { name: /Approve \(simulated\)/ }).click();
  await approve.close();
  const authorized = await api.post(`/payments/orders/${order.id}/authorize`);
  return { agreementId: a.id as string, authId: authorized.authorization.id as string };
}

let agreementId = "";

test("setup (API): verified, reviewed and captured SIMULATED $500", async ({ page, baseURL }) => {
  test.setTimeout(10 * 60_000);
  const { agreementId: id, authId } = await lockedAndAuthorized(page, baseURL!, "P08 refund demo — landing page");
  agreementId = id;
  await login(page, "maya@northwind.demo");
  let api = bff(page, baseURL!);
  const run = await api.post(`/agreements/${id}/evidence`, { delivery_url: `${FIXTURES}/landing-page-fixed/` });
  let r = run;
  for (let i = 0; i < 120 && !["completed", "errored"].includes(r.status); i++) {
    await page.waitForTimeout(3000);
    r = await api.get(`/verification/runs/${run.id}`);
  }
  expect(r.verdict).toBe("needs_manual_review");
  await login(page, "rita@acme.demo");
  api = bff(page, baseURL!);
  const crit = r.results.find((x: { criterion_key: string }) => x.criterion_key === "website_accessible").criterion_id;
  await api.post(`/verification/runs/${r.id}/manual-reviews`, {
    criterion_id: crit,
    decision: "satisfied",
    rationale: "SCRIPTED DEMO DECISION: recorded by the P08 browser test setup. No human performed an accessibility review.",
    evidence_snapshot_sha256: r.evidence_snapshot_sha256,
  });
  await login(page, "bob@acme.demo");
  api = bff(page, baseURL!);
  const pol = await api.get(`/payments/authorizations/${authId}/policy`);
  const appr = await api.post(`/payments/authorizations/${authId}/capture-approvals`, { inputs_sha256: pol.inputs_sha256 });
  const cap = await api.post(`/payments/authorizations/${authId}/captures`, { approval_id: appr.approval_id });
  expect(cap.status).toBe("completed");
  expect(cap.environment).toBe("simulated");
});

test("buyer opens a case in the UI; nothing is refunded", async ({ page }) => {
  await login(page, "alice@acme.demo");
  await page.goto(`/agreements/${agreementId}?tab=resolution`);
  await page.getByRole("button", { name: "Open a case" }).click();
  await shot(page, "01-open-case-step1");
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByLabel("Describe the issue").fill("After launch the contact form shows the thank-you text but messages never reach us.");
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByLabel("Contact form can be submitted").check();
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByLabel(/Partial refund/).check();
  await page.getByLabel(/Requested amount/).fill("150.00");
  await page.getByRole("button", { name: "Continue" }).click();
  await shot(page, "02-open-case-review");
  await page.getByRole("button", { name: "Open case" }).click();
  await expect(page.getByTestId("case-workspace")).toBeVisible();
  await expect(page.getByTestId("financials")).toContainText("$0.00");
  await expect(page.getByText("Open", { exact: true }).first()).toBeVisible();
});

test("provider responds and counter-proposes $100 (with its approval)", async ({ page }) => {
  await login(page, "maya@northwind.demo");
  await page.goto(`/agreements/${agreementId}?tab=resolution`);
  await page.getByRole("button", { name: /A defect appeared after acceptance/ }).click();
  await page.getByPlaceholder(/Write to the other party/).fill("We confirm the issue and offer a partial refund.");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Under review").first()).toBeVisible();
  await page.getByLabel("Remedy").selectOption("partial_refund");
  await page.getByLabel(/^Amount/).fill("100.00");
  await page.getByLabel("Rationale").fill("Refund $100 for the unreliable contact form; the rest of the page was delivered as agreed.");
  await page.getByRole("button", { name: "Submit proposal" }).click();
  await expect(page.getByTestId("proposal-1").getByText("provider: approved by maya@northwind.demo")).toBeVisible();
  await shot(page, "03-provider-proposal");
});

test("buyer approver approves; refund confirmation; SIMULATED refund completes", async ({ page }) => {
  await login(page, "bob@acme.demo");
  await page.goto(`/agreements/${agreementId}?tab=resolution`);
  await page.getByRole("button", { name: /A defect appeared after acceptance/ }).click();
  await page.getByTestId("proposal-1").getByRole("button", { name: "Approve as buyer" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Approve" }).click();
  await expect(page.getByText("Refund policy satisfied")).toBeVisible();
  await shot(page, "04-agreed-policy-satisfied");
  await page.getByRole("button", { name: "Execute refund…" }).click();
  await shot(page, "05-refund-confirmation");
  await page.getByRole("dialog").getByRole("button", { name: "Refund $100.00" }).click();
  await expect(page.getByText("Resolved", { exact: true }).first()).toBeVisible();
  const fin = page.getByTestId("financials");
  await expect(fin).toContainText("$100.00");
  await expect(fin).toContainText("$400.00");
  await expect(page.getByText("SIMULATED").first()).toBeVisible();
  await shot(page, "06-case-resolved");
});

test("ledger shows the resolution and refund trail, simulated labelled", async ({ page }) => {
  await login(page, "alice@acme.demo");
  await page.goto(`/ledger?agreement=${agreementId}&category=resolution`);
  const grid = page.locator(".ag-root");
  await expect(grid.getByText("Resolution case opened")).toBeVisible();
  await expect(grid.getByText("Proposal approved").first()).toBeVisible();
  await page.goto(`/ledger?agreement=${agreementId}&category=payment`);
  await expect(grid.getByText("Refund completed (simulated)")).toBeVisible();
  await shot(page, "07-ledger-refund");
});

test("cancel authorization (void) on a second agreement, through the UI", async ({ page, baseURL }) => {
  test.setTimeout(5 * 60_000);
  const { agreementId: id } = await lockedAndAuthorized(page, baseURL!, "P08 void demo — landing page");
  await login(page, "bob@acme.demo");
  await page.goto(`/agreements/${id}?tab=payment`);
  await expect(page.getByText("It is not a refund: the payment was never captured")).toBeVisible();
  await page.getByRole("button", { name: "Cancel authorization…" }).click();
  await page.getByRole("dialog").getByLabel(/Reason/).fill("Project cancelled before any delivery.");
  await shot(page, "08-void-confirmation");
  await page.getByRole("dialog").getByRole("button", { name: "Cancel authorization" }).click();
  await expect(page.getByText("Authorization cancelled").first()).toBeVisible();
  await expect(page.getByText("No money was captured or transferred.")).toBeVisible();
  await shot(page, "09-void-completed");
});

test("mobile 390 px: case workspace usable", async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const page = await ctx.newPage();
  await login(page, "alice@acme.demo");
  await page.goto(`/agreements/${agreementId}?tab=resolution`);
  await page.getByRole("button", { name: /A defect appeared after acceptance/ }).click();
  await expect(page.getByTestId("financials")).toBeVisible();
  const [sw, iw] = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
  expect(sw).toBeLessThanOrEqual(iw);
  await shot(page, "m01-case-390");
  await ctx.close();
});
