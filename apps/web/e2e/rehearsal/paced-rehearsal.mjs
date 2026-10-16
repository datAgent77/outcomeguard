// Human-PACED automated rehearsal of docs/hackathon/GOLDEN_DEMO.md — an ESTIMATE of a human
// run, not a human rehearsal. Each segment lasts max(narration at WPM, on-screen actions at
// human pace); actions include ~1 s "find the button" pauses, typing at ~9 chars/s and a 2 s
// window switch. Real waits (Claude, Chromium, Lighthouse) are measured, not simulated.
//
// Pre-stage (untimed, like before the audience arrives): one browser profile per account,
// already signed in and on the right page; a --ready prepared agreement (rehearsal).
//   BASE=http://localhost:3010 API=http://localhost:8010 READY=<agreement id> DEMO_PASSWORD=… \
//   WPM=140 node e2e/rehearsal/paced-rehearsal.mjs <label>
import { chromium } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.BASE ?? "http://localhost:3010";
const API = process.env.API ?? "http://localhost:8010";
const READY = process.env.READY;
const PW = process.env.DEMO_PASSWORD;
const WPM = Number(process.env.WPM ?? 140);
const LABEL = process.argv[2] ?? "run";
const OUT = path.resolve(import.meta.dirname, "../../../../docs/hackathon/rehearsals");
if (!READY || !PW) throw new Error("READY and DEMO_PASSWORD required");

// Narration — identical to GOLDEN_DEMO.md.
const N = {
  hook: "Hiring a freelancer today means paying for a promise. Either the buyer pays first and hopes, or the freelancer works first and hopes. OutcomeGuard replaces hope with proof: the money is authorized on PayPal up front, but it is captured only when measurable evidence, and the buyer, say yes.",
  ai: "Acme needs a five hundred dollar landing page. The buyer writes the request in plain English. Claude turns it into measurable acceptance criteria, and every criterion quotes the buyer's own words. Mandatory wording stays blocking, and the AI's own idea is only a suggestion, off by default.",
  lock: "The buyer adds what only they know, the form fields and the deadline, and locks the agreement. Locking freezes a content hash: nobody can quietly move the goalposts.",
  verify: "Now the freelancer delivers. OutcomeGuard opens the page in a real Chromium browser on a phone-sized screen, submits the contact form to a controlled test endpoint, and runs Google Lighthouse. This is happening live.",
  evidence: "Four checks passed, in plain language, with the stored screenshots and the Lighthouse report. Accessibility cannot be measured by a tool, so it waits for an authorized human. AI can explain this evidence, but it never decides.",
  broken: "Here is the same check on the first, broken delivery, recorded before this session: the form was submitted, but the success message never appeared. Capture was blocked.",
  capture: "On this prepared agreement the review is done and the payment is authorized, simulated here, as the banner says. The policy is re-checked live: eligible. The buyer approves, then captures: two separate, explicit actions.",
  proof: "And this is not only a simulation. The same flow ran on PayPal Sandbox: a governed capture, a refund both parties approved, and a cancelled authorization, each read back from PayPal. Don't just pay for a promise. Pay for proof.",
};
const words = (t) => t.trim().split(/\s+/).length;
const speak = (t) => (words(t) / WPM) * 60_000;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const THINK = Number(process.env.THINK_MS ?? 900);

async function apiLogin(email) {
  const r = await fetch(`${API}/auth/login`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ email, password: PW }) });
  return (await r.json()).token;
}
async function apiGet(tok, p) {
  return (await fetch(`${API}${p}`, { headers: { authorization: `Bearer ${tok}` } })).json();
}

const browser = await chromium.launch();
async function profile(email) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, baseURL: BASE });
  const page = await ctx.newPage();
  if (email) {
    await page.goto("/login");
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password").fill(PW);
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.waitForURL(/\/(dashboard|provider)/);
  }
  return page;
}
const click = async (loc) => {
  await sleep(THINK);
  await loc.click();
};
const type = async (loc, text) => {
  await sleep(THINK);
  await loc.click();
  await loc.pressSequentially(text, { delay: 110 });
};

// ---------------------------------------------------------------- pre-stage (untimed)
const tok = await apiLogin("alice@acme.demo");
const runs = await apiGet(tok, `/agreements/${READY}/verification-runs`);
const broken = runs.find((r) => r.delivery_url.includes("broken"));
const landing = await profile(null);
await landing.goto("/");
const alice = await profile("alice@acme.demo");
await alice.goto("/agreements/new");
const aliceBroken = await (await browser.newContext({ storageState: await alice.context().storageState(), viewport: { width: 1440, height: 900 }, baseURL: BASE })).newPage();
await aliceBroken.goto(`/agreements/${READY}?tab=evidence&run=${broken.id}`);
await aliceBroken.getByText("Verification Report").waitFor();
const maya = await profile("maya@northwind.demo");
const bob = await profile("bob@acme.demo");
await bob.goto(`/agreements/${READY}?tab=payment`);
await bob.getByText("Eligible for buyer approval").waitFor({ timeout: 30_000 });

// ---------------------------------------------------------------- timed run
const segs = [];
const t0 = Date.now();
async function segment(name, narration, actions, { switchWindow = false } = {}) {
  const s = Date.now();
  if (switchWindow) await sleep(2000);
  await Promise.all([actions(), sleep(speak(narration))]);
  segs.push({ name, seconds: +((Date.now() - s) / 1000).toFixed(1), narration_s: +(speak(narration) / 1000).toFixed(1), words: words(narration), started_at_s: +((s - t0) / 1000).toFixed(1) });
  console.log(JSON.stringify(segs.at(-1)));
}
let newAgreementPath = "";
const timing = {};
try {
  await segment("1 hook — landing", N.hook, async () => {
    await sleep(THINK);
    await landing.mouse.wheel(0, 500);
  });
  await segment("2 AI criteria (live Claude)", N.ai, async () => {
    await click(alice.getByRole("button", { name: "Use the example request" }));
    const t = Date.now();
    await click(alice.getByRole("button", { name: "Generate Acceptance Criteria" }));
    await alice.getByText("Requirements from your request").waitFor({ timeout: 120_000 });
    timing.ai_draft_s = +((Date.now() - t) / 1000).toFixed(1);
  }, { switchWindow: true });
  await segment("3 configure + lock", N.lock, async () => {
    await click(alice.getByRole("button", { name: "Continue to configuration" }));
    for (const cb of await alice.getByRole("checkbox", { name: /Require HTTPS/ }).all()) await click(cb);
    for (const b of await alice.getByRole("button", { name: "Use the standard field set" }).all()) await click(b);
    for (const sc of await alice.getByLabel("Minimum performance score (0–100)").all()) if (!(await sc.inputValue())) await type(sc, "90");
    await type(alice.getByLabel("Title"), "Spring campaign landing page");
    await sleep(THINK);
    await alice.getByLabel("Delivery deadline").fill(new Date(Date.now() + 14 * 86_400_000).toISOString().slice(0, 10));
    await click(alice.getByRole("button", { name: "Create draft agreement" }));
    await click(alice.getByRole("button", { name: "Propose this version" }));
    await click(alice.getByRole("button", { name: "Approve and lock…" }));
    await click(alice.getByRole("dialog").getByRole("button", { name: "Approve and lock" }));
    await alice.getByText("Locked", { exact: true }).first().waitFor();
    newAgreementPath = new URL(await alice.getByRole("link", { name: /Open agreement workspace/ }).getAttribute("href"), BASE).pathname;
  });
  await segment("4 provider delivers (live Chromium + Lighthouse)", N.verify, async () => {
    await maya.goto(`${newAgreementPath}?tab=delivery`); // presenter: click the new agreement in the provider list
    await type(maya.getByLabel("Delivery URL"), "http://127.0.0.1:8020/landing-page-fixed/");
    const t = Date.now();
    await click(maya.getByRole("button", { name: "Submit evidence for verification" }));
    await maya.getByRole("button", { name: "Open report" }).first().waitFor({ timeout: 180_000 });
    timing.verification_fixed_s = +((Date.now() - t) / 1000).toFixed(1);
  }, { switchWindow: true });
  await segment("5 evidence report", N.evidence, async () => {
    await click(maya.getByRole("button", { name: "Open report" }).first());
    await maya.getByRole("heading", { name: "Needs human review" }).waitFor();
    await sleep(THINK);
    await maya.mouse.wheel(0, 700);
  });
  await segment("6 broken FAIL (recorded earlier)", N.broken, async () => {
    await sleep(THINK);
    await aliceBroken.mouse.wheel(0, 600);
  }, { switchWindow: true });
  await segment("7 policy + approve + capture (SIMULATED)", N.capture, async () => {
    await click(bob.getByRole("button", { name: "Approve capture…" }));
    await click(bob.getByRole("dialog").getByRole("button", { name: "I approve the capture" }));
    await bob.getByText("Capture approved by the buyer").waitFor();
    await click(bob.getByRole("button", { name: /Capture \$500\.00 via PayPal/ }));
    await click(bob.getByRole("dialog").getByRole("button", { name: "Capture payment" }));
    await bob.getByText("Payment captured (SIMULATED)").waitFor({ timeout: 30_000 });
  }, { switchWindow: true });
  await segment("8 real Sandbox proof + close", N.proof, async () => {
    await sleep(THINK);
    await landing.getByRole("heading", { name: "Proven end-to-end on PayPal Sandbox" }).scrollIntoViewIfNeeded();
  }, { switchWindow: true });
} catch (e) {
  console.log(`FAILED: ${e.message}`);
  process.exitCode = 1;
} finally {
  const total = +((Date.now() - t0) / 1000).toFixed(1);
  const result = { label: LABEL, kind: "human-paced automated rehearsal (estimate, NOT a human run)", wpm: WPM, think_ms: THINK, total_s: total, measured: timing, segments: segs, new_agreement: newAgreementPath, prepared_agreement: READY };
  fs.mkdirSync(OUT, { recursive: true });
  fs.writeFileSync(path.join(OUT, `${LABEL}.json`), JSON.stringify(result, null, 2));
  console.log(`TOTAL ${total} s`);
  await browser.close();
}
