// REAL API in PAYMENT_MODE=sandbox — preparation only, NO PayPal call: two locked
// agreements (scenario 1 verified + scripted review; scenario 2 just locked). Orders,
// approvals, captures, refunds and voids are started only after the user says READY.
// Opt-in: E2E_P08_PREP=1.
import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const PASSWORD = process.env.DEMO_PASSWORD ?? "";
const FIXTURES = process.env.FIXTURES_URL ?? "http://127.0.0.1:8020";
const OUT = path.resolve(__dirname, "../../test-results/p08-sandbox-prep.json");

test.skip(process.env.E2E_P08_PREP !== "1" || !PASSWORD, "opt-in: E2E_P08_PREP=1, E2E_BASE_URL, DEMO_PASSWORD");

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

async function login(page: Page, email: string) {
  await page.context().clearCookies();
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/(dashboard|provider)/);
}

test("prepare two Sandbox-mode agreements (no PayPal calls)", async ({ page, baseURL }) => {
  test.setTimeout(10 * 60_000);
  const H = { "x-og-csrf": "1", origin: baseURL!, "content-type": "application/json" };
  const call = async (method: "get" | "post", p: string, data?: unknown) => {
    const r = method === "get" ? await page.request.get(`/api/og${p}`) : await page.request.post(`/api/og${p}`, { data: data ?? {}, headers: H });
    expect(r.ok(), `${p} ${r.status()} ${await r.text()}`).toBeTruthy();
    return r.json();
  };
  await login(page, "alice@acme.demo");
  const me = await call("get", "/workspace/context");
  const merchants = await call("get", "/workspace/merchants");
  expect(merchants[0].environment).toBe("sandbox");
  const out: Record<string, string> = {};
  for (const [name, title] of [
    ["refund", "P08 Sandbox — refund scenario (landing page)"],
    ["void", "P08 Sandbox — void scenario (landing page)"],
  ]) {
    const a = await call("post", "/agreements", {
      buyer_organization_id: me.organizations[0].id,
      merchant_account_id: merchants[0].id,
      title,
      requirements_text: "Build a mobile-friendly landing page for $500. The contact form must work and the website must be accessible.",
      amount: "500.00",
      delivery_deadline: new Date(Date.now() + 14 * 86_400_000).toISOString(),
      criteria: CRITERIA,
    });
    const v = await call("post", `/agreements/${a.id}/versions/1/propose`, { expected_revision: a.versions[0].revision });
    await call("post", `/agreements/${a.id}/versions/1/approve`, { content_hash: v.versions[0].content_hash });
    out[name] = a.id;
  }
  await login(page, "maya@northwind.demo");
  const run = await call("post", `/agreements/${out.refund}/evidence`, { delivery_url: `${FIXTURES}/landing-page-fixed/` });
  let r = run;
  for (let i = 0; i < 120 && !["completed", "errored"].includes(r.status); i++) {
    await page.waitForTimeout(3000);
    r = await call("get", `/verification/runs/${run.id}`);
  }
  expect(r.verdict).toBe("needs_manual_review");
  await login(page, "rita@acme.demo");
  const crit = r.results.find((x: { criterion_key: string }) => x.criterion_key === "website_accessible").criterion_id;
  await call("post", `/verification/runs/${r.id}/manual-reviews`, {
    criterion_id: crit,
    decision: "satisfied",
    rationale: "SCRIPTED DEMO DECISION: recorded by the P08 Sandbox preparation script. No human performed an accessibility review.",
    evidence_snapshot_sha256: r.evidence_snapshot_sha256,
  });
  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, JSON.stringify({ ...out, refund_run: r.id, prepared_at: new Date().toISOString() }, null, 2));
});
