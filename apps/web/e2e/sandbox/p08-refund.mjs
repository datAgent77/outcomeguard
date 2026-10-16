// P08 real PayPal Sandbox scenario 1, driven through the UI by an automated browser.
// Every in-app step is SCRIPTED (demo accounts); the PayPal buyer approval is done by a
// person. Phases: `start` (create the order, print the PayPal approval URL) and `finish`
// (authorize → governed capture → case → $100 refund agreed by both sides → execute).
// Stops on any uncertain outcome; never re-sends.
//   BASE=http://localhost:3010 AGREEMENT=<id> DEMO_PASSWORD=… node e2e/sandbox/p08-refund.mjs start|finish
import { chromium } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.BASE ?? "http://localhost:3010";
const AG = process.env.AGREEMENT;
const PW = process.env.DEMO_PASSWORD;
const SHOTS = path.resolve(import.meta.dirname, "../../../../docs/p08/sandbox");
const LOG = [];
const t0 = Date.now();
const step = (what, actor, facts = {}) => {
  const e = { t: new Date().toISOString(), what, actor, ...facts };
  LOG.push(e);
  console.log(JSON.stringify(e));
};
if (!AG || !PW) throw new Error("AGREEMENT and DEMO_PASSWORD required");

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, baseURL: BASE });
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));

async function login(email) {
  await ctx.clearCookies();
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PW);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(/\/(dashboard|provider)/);
}
async function api(p) {
  const r = await page.request.get(`/api/og${p}`);
  if (!r.ok()) throw new Error(`${p}: ${r.status()}`);
  return r.json();
}
async function shot(name) {
  fs.mkdirSync(SHOTS, { recursive: true });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true });
}
function expect(cond, msg) {
  if (!cond) throw new Error(`STOP: ${msg}`);
}
const S = (who) => `SCRIPTED demo account ${who} (automated browser)`;

try {
  const phase = process.argv[2];
  if (phase === "start") {
    await login("alice@acme.demo");
    const existing = await api(`/agreements/${AG}/payments`);
    expect(existing.length === 0, `agreement already has ${existing.length} order(s) — not creating another`);
    await page.goto(`/agreements/${AG}?tab=payment`);
    await page.getByRole("button", { name: "Start PayPal Sandbox checkout" }).click();
    const link = page.getByRole("link", { name: /Open PayPal approval/ });
    await link.waitFor();
    const orders = await api(`/agreements/${AG}/payments`);
    expect(orders.length === 1, "exactly one order expected");
    const o = orders[0];
    expect(o.environment === "sandbox" && !o.simulated, `environment is ${o.environment}`);
    expect(o.amount === "500.00" && o.currency === "USD", `amount ${o.amount} ${o.currency}`);
    step("PayPal Sandbox order created via UI", S("alice@acme.demo"), { paypal_order_id: o.paypal_order_id, amount: `${o.amount} ${o.currency}`, environment: o.environment });
    console.log(`APPROVAL_URL ${await link.getAttribute("href")}`);
  } else if (phase === "finish") {
    // 1. Authorize (buyer admin)
    await login("alice@acme.demo");
    await page.goto(`/agreements/${AG}?tab=payment`);
    // The PayPal return page may already have recorded the approval (read from PayPal).
    const check = page.getByRole("button", { name: "Check approval status" });
    if (await check.isVisible().catch(() => false)) await check.click();
    await page.getByRole("button", { name: "Authorize payment" }).click({ timeout: 30000 });
    await page.getByText("Payment is authorized, not captured. Authorization can expire.").waitFor();
    let o = (await api(`/agreements/${AG}/payments`))[0];
    expect(o.environment === "sandbox" && o.authorization?.amount === "500.00" && o.authorization.status === "authorized", "authorization check failed");
    step("Authorized (real Sandbox)", S("alice@acme.demo"), { paypal_authorization_id: o.authorization.paypal_authorization_id, amount: "500.00 USD", expires_at: o.authorization.expires_at });
    // 2. Governed capture (buyer approver)
    await login("bob@acme.demo");
    await page.goto(`/agreements/${AG}?tab=payment`);
    await page.getByText("Eligible for buyer approval").waitFor({ timeout: 30000 });
    const pol = await api(`/payments/authorizations/${o.authorization.id}/policy`);
    expect(pol.decision === "eligible_for_approval", `policy ${pol.decision}`);
    step("P06 policy evaluated", S("bob@acme.demo"), { decision: pol.decision, inputs_sha256: pol.inputs_sha256, run: pol.verification_run_id });
    await page.getByRole("button", { name: "Approve capture…" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "I approve the capture" }).click();
    await page.getByText("Capture approved by the buyer").waitFor();
    const appr = (await api(`/payments/authorizations/${o.authorization.id}/capture-approvals`))[0];
    step("Capture approval recorded", S("bob@acme.demo"), { approval_id: appr.id, bound_inputs_sha256: appr.inputs_sha256 });
    await page.getByRole("button", { name: /Capture \$500\.00 via PayPal/ }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Capture payment" }).click();
    await page.getByText(/^Payment captured$|PayPal outcome unknown|Capture rejected/).first().waitFor({ timeout: 90000 });
    o = (await api(`/agreements/${AG}/payments`))[0];
    expect(o.capture?.status === "completed", `capture is ${o.capture?.status} — STOPPING (no retry)`);
    expect(o.capture.amount === "500.00" && o.environment === "sandbox", "capture amount/environment mismatch");
    step("Captured (real Sandbox)", S("bob@acme.demo") + " requests; SYSTEM calls PayPal", { paypal_capture_id: o.capture.paypal_capture_id, amount: "500.00 USD", paypal_fee: o.capture.settlement.paypal_fee, net: o.capture.settlement.net_at_capture });
    await shot("01-captured");
    // 3. Buyer opens a case
    await login("alice@acme.demo");
    await page.goto(`/agreements/${AG}?tab=resolution`);
    await page.getByRole("button", { name: "Open a case" }).click();
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByLabel("Describe the issue").fill("SCRIPTED P08 Sandbox demo: the contact form intermittently fails after launch; partial refund requested.");
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByLabel("Contact form can be submitted").check();
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByLabel(/Partial refund/).check();
    await page.getByLabel(/Requested amount/).fill("100.00");
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: "Open case" }).click();
    await page.getByTestId("case-workspace").waitFor();
    const cases = await api(`/agreements/${AG}/resolutions`);
    const caseId = cases[0].id;
    step("Resolution case opened (no money moved)", S("alice@acme.demo"), { case_id: caseId });
    // 4. Provider proposes $100 with its approval
    await login("maya@northwind.demo");
    await page.goto(`/agreements/${AG}?tab=resolution&case=${caseId}`);
    await page.getByTestId("case-workspace").waitFor();
    await page.getByLabel("Remedy").selectOption("partial_refund");
    await page.getByLabel(/^Amount/).fill("100.00");
    await page.getByLabel("Rationale").fill("SCRIPTED P08 Sandbox demo: provider agrees to refund $100 for the contact form issue.");
    await page.getByRole("button", { name: "Submit proposal" }).click();
    await page.getByTestId("proposal-1").getByText("provider: approved by maya@northwind.demo").waitFor();
    let c = await api(`/resolutions/${caseId}`);
    const prop = c.proposals[0];
    expect(prop.amount === "100.00" && prop.currency === "USD", "proposal amount mismatch");
    step("Proposal #1 submitted + provider approval", S("maya@northwind.demo"), { proposal_id: prop.id, amount: "100.00 USD", content_sha256: prop.content_sha256 });
    // 5. Buyer approver approves and executes
    await login("bob@acme.demo");
    await page.goto(`/agreements/${AG}?tab=resolution&case=${caseId}`);
    await page.getByTestId("proposal-1").getByRole("button", { name: "Approve as buyer" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Approve" }).click();
    await page.getByText("Refund policy satisfied").waitFor();
    step("Buyer approval recorded; refund policy allowed", S("bob@acme.demo"), { case_status: (await api(`/resolutions/${caseId}`)).status });
    await shot("02-agreed");
    c = await api(`/resolutions/${caseId}`);
    expect(c.financials.remaining === "500.00" && c.financials.captured === "500.00", "unexpected balance before refund");
    await page.getByRole("button", { name: "Execute refund…" }).click();
    await shot("03-refund-confirmation");
    await page.getByRole("dialog").getByRole("button", { name: "Refund $100.00" }).click();
    await page.getByText(/^Resolved$|A refund's outcome is unknown|REFUND|POLICY/).first().waitFor({ timeout: 90000 });
    c = await api(`/resolutions/${caseId}`);
    const ref = c.financials.refunds[0];
    expect(ref && ref.status === "completed", `refund is ${ref?.status} — STOPPING (no re-send)`);
    expect(ref.amount === "100.00" && ref.environment === "sandbox", "refund amount/environment mismatch");
    step("Refund completed (real Sandbox)", S("bob@acme.demo") + " executes; SYSTEM calls PayPal", { paypal_refund_id: ref.paypal_refund_id, amount: "100.00 USD", paypal_status: ref.paypal_status, fee_returned: ref.paypal_fee_returned, merchant_debit: ref.merchant_debit, case_status: c.status, remaining: c.financials.remaining });
    await page.reload();
    await page.getByTestId("financials").waitFor();
    await shot("04-case-resolved");
    await login("alice@acme.demo");
    await page.goto(`/ledger?agreement=${AG}`);
    await page.locator(".ag-root").waitFor();
    await shot("05-ledger");
    fs.writeFileSync(path.join(SHOTS, "scenario1.json"), JSON.stringify({ agreement: AG, case_id: caseId, log: LOG, console_errors: errors }, null, 2));
  } else throw new Error("phase: start | finish");
} catch (e) {
  console.log(`STOPPED: ${e.message}`);
  process.exitCode = 1;
} finally {
  if (errors.length) console.log(`PAGE_ERRORS ${JSON.stringify(errors)}`);
  await browser.close();
  void t0;
}
