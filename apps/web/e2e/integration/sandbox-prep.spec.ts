// REAL API, REAL PayPal SANDBOX MODE — preparation only. Creates a locked agreement (real AI
// draft), verifies the fixed fixture page and records the scripted demo review, then STOPS.
// It never starts a checkout, authorizes or captures: those are done by a person in the UI
// (docs/P07_UI_VERIFICATION.md, "Real Sandbox run"). Opt-in: E2E_SANDBOX_PREP=1.
import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const PASSWORD = process.env.DEMO_PASSWORD ?? "";
const FIXTURES = process.env.FIXTURES_URL ?? "http://127.0.0.1:8020";
const OUT = path.resolve(__dirname, "../../test-results/sandbox-prep.json");
const SCRIPTED =
  "SCRIPTED DEMO DECISION: recorded by the P07 Sandbox preparation script through the reviewer demo account. No human performed an accessibility review.";

test.skip(process.env.E2E_SANDBOX_PREP !== "1" || !PASSWORD, "opt-in: E2E_SANDBOX_PREP=1, E2E_BASE_URL, DEMO_PASSWORD");

test("prepare a Sandbox-mode agreement up to the payment step", async ({ page }) => {
  test.setTimeout(12 * 60_000);
  const login = async (email: string) => {
    await page.context().clearCookies();
    await page.goto("/login");
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page).toHaveURL(/\/(dashboard|provider)/);
  };
  await login("alice@acme.demo");
  await page.goto("/agreements/new");
  await page.getByLabel("Request").fill(
    "Build a mobile-friendly landing page for $500. The contact form must work, the website must be accessible and Lighthouse performance must score at least 90.",
  );
  await page.getByRole("button", { name: "Generate Acceptance Criteria" }).click();
  await expect(page.getByText("Requirements from your request")).toBeVisible({ timeout: 180_000 });
  await page.getByRole("button", { name: "Continue to configuration" }).click();
  for (const cb of await page.getByRole("checkbox", { name: /Require HTTPS/ }).all()) await cb.uncheck();
  for (const b of await page.getByRole("button", { name: "Use the standard field set" }).all()) await b.click();
  for (const s of await page.getByLabel("Minimum performance score (0–100)").all()) if (!(await s.inputValue())) await s.fill("90");
  for (const i of await page.getByLabel("Instructions for the human reviewer").all()) if (!(await i.inputValue())) await i.fill("Check WCAG 2.2 AA");
  await page.getByLabel("Title").fill("Sandbox demo — spring campaign landing page");
  if (!(await page.getByLabel("Price (USD)").inputValue())) await page.getByLabel("Price (USD)").fill("500");
  await page.getByLabel("Delivery deadline").fill(new Date(Date.now() + 14 * 86_400_000).toISOString().slice(0, 10));
  await page.getByRole("button", { name: "Create draft agreement" }).click();
  await page.getByRole("button", { name: "Propose this version" }).click();
  await page.getByRole("button", { name: "Approve and lock…" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Approve and lock" }).click();
  await expect(page.getByText("Locked", { exact: true }).first()).toBeVisible();
  const url = new URL((await page.getByRole("link", { name: /Open agreement workspace/ }).getAttribute("href"))!, page.url()).pathname;

  await login("maya@northwind.demo");
  await page.goto(`${url}?tab=delivery`);
  await page.getByLabel("Delivery URL").fill(`${FIXTURES}/landing-page-fixed/`);
  await page.getByRole("button", { name: "Submit evidence for verification" }).click();
  await expect(page.getByRole("button", { name: "Open report" })).toHaveCount(1, { timeout: 6 * 60_000 });
  await page.getByRole("button", { name: "Open report" }).click();
  await expect(page.getByRole("heading", { name: "Needs human review" })).toBeVisible();

  await login("rita@acme.demo");
  await page.goto(`${url}?tab=evidence`);
  const panel = page.getByTestId("manual-review-panel");
  await panel.getByLabel("Satisfied", { exact: true }).check();
  await panel.getByLabel(/Rationale/).fill(SCRIPTED);
  await panel.getByLabel(/I personally reviewed/).check();
  await panel.getByRole("button", { name: "Record decision…" }).click();
  await page.getByRole("dialog").getByRole("button", { name: /Record “Satisfied”/ }).click();
  await expect(page.getByText("Scripted demo decision — not a human review")).toBeVisible();
  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, JSON.stringify({ agreement_path: url, prepared_at: new Date().toISOString() }));
});
