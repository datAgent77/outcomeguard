// Records the product video from the REAL running app (rehearsal mode — payments SIMULATED)
// as SILENT, captioned segments; the voice-over is added afterwards by mux-voice.mjs.
// Nothing is faked: Claude and Chromium/Lighthouse run live; waits are sped up with an
// on-screen "real wait" label; the broken FAIL is labelled RECORDED EARLIER; the capture is
// labelled SIMULATED; Sandbox IDs come from the recorded Oct 16 runs.
//   BASE=http://localhost:3010 READY=<prepare_demo --ready id> DEMO_PASSWORD=… node e2e/video/build-video.mjs
// Output: docs/hackathon/video/segments/<id>.mp4 + manifest.json, and a silent preview.
import { chromium } from "@playwright/test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { SEG, estimate } from "./segments.mjs";

const BASE = process.env.BASE ?? "http://localhost:3010";
const API = process.env.API ?? "http://localhost:8010";
const READY = process.env.READY;
const PW = process.env.DEMO_PASSWORD;
const VIDEO = path.resolve(import.meta.dirname, "../../../../docs/hackathon/video");
const OUT = path.join(VIDEO, "build");
const SEGDIR = path.join(VIDEO, "segments");
if (!READY || !PW) throw new Error("READY and DEMO_PASSWORD required");
fs.rmSync(OUT, { recursive: true, force: true });
fs.mkdirSync(OUT, { recursive: true });
fs.rmSync(SEGDIR, { recursive: true, force: true });
fs.mkdirSync(SEGDIR, { recursive: true });
const W = 1920, H = 1080, VW = 1280, VH = 720;
const sh = (cmd, args) => execFileSync(cmd, args, { stdio: ["ignore", "pipe", "pipe"] }).toString();
const dur = (f) => Number(sh("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f]).trim());
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ------------------------------------------------------------------ 1. expected narration length
for (const s of SEG) {
  s.audio = estimate(s.say);
  console.log(`expected narration ${s.id}: ${s.audio.toFixed(1)} s`);
}

// ------------------------------------------------------------------ 2. overlays
const browser = await chromium.launch();
const COLORS = { live: "#13804f", recorded: "#33445f", simulated: "#b26a00", real: "#0f6b46" };
async function overlay(file, { badge, caption, extra }) {
  const p = await browser.newPage({ viewport: { width: W, height: H } });
  await p.setContent(`<!doctype html><html><head><style>
    html,body{margin:0;background:transparent;font-family:-apple-system,"SF Pro Display","Helvetica Neue",Arial,sans-serif}
    .badge{position:absolute;left:48px;top:40px;padding:12px 22px;border-radius:999px;color:#fff;font-weight:700;font-size:28px;letter-spacing:.06em;box-shadow:0 6px 20px rgba(0,0,0,.25)}
    .cap{position:absolute;left:50%;bottom:54px;transform:translateX(-50%);max-width:1640px;padding:20px 34px;border-radius:18px;background:rgba(10,22,40,.88);color:#fff;font-size:36px;font-weight:600;line-height:1.3;text-align:center;box-shadow:0 10px 30px rgba(0,0,0,.3)}
    .extra{position:absolute;right:48px;top:40px;padding:12px 22px;border-radius:14px;background:rgba(41,82,212,.95);color:#fff;font-size:28px;font-weight:700}
  </style></head><body>
    ${badge && badge[0] ? `<div class="badge" style="background:${COLORS[badge[1]]}">${badge[0]}</div>` : ""}
    ${caption ? `<div class="cap">${caption}</div>` : ""}
    ${extra ? `<div class="extra">${extra}</div>` : ""}
  </body></html>`);
  await p.screenshot({ path: file, omitBackground: true });
  await p.close();
}
async function card(file) {
  const p = await browser.newPage({ viewport: { width: W, height: H } });
  await p.setContent(`<!doctype html><html><body style="margin:0;height:100vh;display:flex;align-items:center;justify-content:center;background:#0f1f3a;font-family:-apple-system,'SF Pro Display','Helvetica Neue',Arial,sans-serif;color:#fff">
    <div style="text-align:center">
      <div style="font-size:84px;font-weight:700;letter-spacing:-.02em">OutcomeGuard</div>
      <div style="margin-top:22px;font-size:46px;color:#c3cad6">Don't just pay for a promise. <span style="color:#9db4f7">Pay for proof.</span></div>
      <div style="margin-top:60px;font-size:30px;color:#8f9bae">github.com/datAgent77/outcomeguard · MIT · PayPal Sandbox · Claude</div>
    </div></body></html>`);
  await p.screenshot({ path: file });
  await p.close();
}

// ------------------------------------------------------------------ 3. recordings
async function state(email) {
  const ctx = await browser.newContext({ baseURL: BASE });
  const p = await ctx.newPage();
  await p.goto("/login");
  await p.getByLabel("Email").fill(email);
  await p.getByLabel("Password").fill(PW);
  await p.getByRole("button", { name: "Sign in" }).click();
  await p.waitForURL(/dashboard|provider/);
  const st = await ctx.storageState();
  await ctx.close();
  return st;
}
const ST = { alice: await state("alice@acme.demo"), maya: await state("maya@northwind.demo"), bob: await state("bob@acme.demo") };
const tok = await (await fetch(`${API}/auth/login`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ email: "alice@acme.demo", password: PW }) })).json();
const runs = await (await fetch(`${API}/agreements/${READY}/verification-runs`, { headers: { authorization: `Bearer ${tok.token}` } })).json();
const brokenRun = runs.find((r) => r.delivery_url.includes("broken")).id;

async function take(seg, who, startUrl, actions) {
  const ctx = await browser.newContext({
    baseURL: BASE,
    viewport: { width: VW, height: VH },
    deviceScaleFactor: 1.5,
    storageState: who ? ST[who] : undefined,
    recordVideo: { dir: OUT, size: { width: VW, height: VH } },
  });
  const page = await ctx.newPage();
  const t0 = Date.now();
  await page.goto(startUrl);
  await page.waitForLoadState("networkidle").catch(() => {});
  await sleep(600);
  const marks = { start: (Date.now() - t0) / 1000 };
  const rel = () => (Date.now() - t0) / 1000;
  const minEnd = Date.now() + seg.audio * 1000 * 0.92;
  await actions(page, marks, rel);
  while (Date.now() < minEnd) await sleep(100);
  await sleep(500);
  marks.end = rel();
  const v = page.video();
  await ctx.close();
  seg.raw = await v.path();
  seg.marks = marks;
  console.log(`recorded ${seg.id}: ${JSON.stringify(marks)}`);
}
const human = async (loc) => {
  await sleep(700);
  await loc.scrollIntoViewIfNeeded().catch(() => {});
  await sleep(250);
  await loc.click();
};
const glide = async (page, px, ms) => {
  const steps = Math.max(1, Math.round(ms / 40));
  for (let i = 0; i < steps; i++) {
    await page.mouse.wheel(0, px / steps);
    await sleep(40);
  }
};

const S = Object.fromEntries(SEG.map((s) => [s.id, s]));
let newAgreement = "";
await take(S.s1, null, "/", async (page) => {
  await page.mouse.move(700, 400);
});
await take(S.s2, null, "/", async (page) => {
  await sleep(800);
  await glide(page, 1500, S.s2.audio * 1000 * 0.8);
});
await take(S.s3, "alice", "/agreements/new", async (page, m, rel) => {
  await human(page.getByRole("button", { name: "Use the example request" }));
  await sleep(1200);
  await human(page.getByRole("button", { name: "Generate Acceptance Criteria" }));
  m.waitStart = rel();
  await page.getByText("Requirements from your request").waitFor({ timeout: 120000 });
  m.waitEnd = rel();
  await sleep(1200);
  await glide(page, 900, 5500);
  await human(page.getByRole("button", { name: "Continue to configuration" }));
  for (const cb of await page.getByRole("checkbox", { name: /Require HTTPS/ }).all()) await human(cb);
  for (const b of await page.getByRole("button", { name: "Use the standard field set" }).all()) await human(b);
  for (const sc of await page.getByLabel("Minimum performance score (0–100)").all()) if (!(await sc.inputValue())) await sc.fill("90");
  await sleep(500);
  await page.getByLabel("Title").pressSequentially("Spring campaign landing page", { delay: 45 });
  await page.getByLabel("Delivery deadline").fill(new Date(Date.now() + 14 * 86400000).toISOString().slice(0, 10));
  await human(page.getByRole("button", { name: "Create draft agreement" }));
  await human(page.getByRole("button", { name: "Propose this version" }));
  await human(page.getByRole("button", { name: "Approve and lock…" }));
  await sleep(1500);
  await human(page.getByRole("dialog").getByRole("button", { name: "Approve and lock" }));
  await page.getByText("Locked", { exact: true }).first().waitFor();
  newAgreement = new URL(await page.getByRole("link", { name: /Open agreement workspace/ }).getAttribute("href"), BASE).pathname;
  await sleep(1500);
});
await take(S.s4, "maya", `${newAgreement}?tab=delivery`, async (page, m, rel) => {
  await sleep(600);
  await page.getByLabel("Delivery URL").click();
  await page.getByLabel("Delivery URL").pressSequentially("http://127.0.0.1:8020/landing-page-fixed/", { delay: 35 });
  await human(page.getByRole("button", { name: "Submit evidence for verification" }));
  m.waitStart = rel();
  await page.getByRole("button", { name: "Open report" }).first().waitFor({ timeout: 180000 });
  m.waitEnd = rel();
  await sleep(600);
  await human(page.getByRole("button", { name: "Open report" }).first());
  await page.getByRole("heading", { name: "Needs human review" }).waitFor();
  await sleep(2200);
  await glide(page, 1400, 9000);
});
await take(S.s5, "alice", `/agreements/${READY}?tab=evidence&run=${brokenRun}`, async (page) => {
  await sleep(1500);
  await page.getByTestId("result-contact_form_works").scrollIntoViewIfNeeded();
  await page.evaluate(() => window.scrollBy(0, -120));
});
await take(S.s6, "bob", `/agreements/${READY}?tab=evidence`, async (page) => {
  await page.getByText("Scripted demo decision — not a human review").scrollIntoViewIfNeeded();
  await page.evaluate(() => window.scrollBy(0, -200));
  await sleep(3500);
  await human(page.getByRole("tab", { name: "Payment" }));
  await page.getByText("Eligible for buyer approval").waitFor({ timeout: 30000 });
  await sleep(1800);
  await human(page.getByRole("button", { name: "Approve capture…" }));
  await sleep(1200);
  await human(page.getByRole("dialog").getByRole("button", { name: "I approve the capture" }));
  await page.getByText("Capture approved by the buyer").waitFor();
  await human(page.getByRole("button", { name: /Capture \$500\.00 via PayPal/ }));
  await sleep(1000);
  await human(page.getByRole("dialog").getByRole("button", { name: "Capture payment" }));
  await page.getByText("Payment captured (SIMULATED)").waitFor({ timeout: 30000 });
  await page.getByText("Payment captured (SIMULATED)").scrollIntoViewIfNeeded();
  await sleep(1500);
});
await take(S.s7, null, "/", async (page) => {
  const h = page.getByRole("heading", { name: "Proven end-to-end on PayPal Sandbox" });
  await h.waitFor();
  await sleep(800);
  const y = await h.evaluate((el) => el.getBoundingClientRect().top + window.scrollY - 90);
  await glide(page, y, 1500);
});

// ------------------------------------------------------------------ 4. silent segments
const manifest = [];
const preview = [];
for (const s of SEG) {
  const segFile = path.join(SEGDIR, `${s.id}.mp4`);
  if (s.card) {
    const png = path.join(SEGDIR, `${s.id}.png`);
    await card(png);
    manifest.push({ id: s.id, card: true, png: path.basename(png), expected_voice_s: +s.audio.toFixed(1) });
    const d = (s.audio + 1.5).toFixed(2);
    const pv = path.join(OUT, `${s.id}-preview.mp4`);
    sh("ffmpeg", ["-y", "-loop", "1", "-t", d, "-i", png, "-f", "lavfi", "-t", d, "-i", "anullsrc=r=48000:cl=stereo", "-filter_complex",
      `[0:v]scale=${W}:${H},fps=30,format=yuv420p,fade=t=in:st=0:d=0.4[v]`,
      "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-c:a", "aac", pv]);
    preview.push(pv);
    continue;
  }
  const capPng = path.join(OUT, `${s.id}-cap.png`);
  await overlay(capPng, { badge: s.badge, caption: s.caption });
  const m = s.marks;
  // timeline: [start, waitStart] 1x · [waitStart, waitEnd] sped up to ~3.5 s · [waitEnd, end] 1x
  let vchain, ffInputs = ["-i", s.raw, "-i", capPng];
  if (m.waitStart != null) {
    const real = m.waitEnd - m.waitStart;
    const k = Math.max(1, real / 3.5);
    const fast = real / k;
    const notePng = path.join(OUT, `${s.id}-note.png`);
    await overlay(notePng, { extra: `⏩ sped up ${k.toFixed(1)}× · real wait ${real.toFixed(0)} s` });
    ffInputs.push("-i", notePng);
    const a = m.waitStart - m.start;
    vchain =
      `[0:v]trim=${m.start}:${m.waitStart},setpts=PTS-STARTPTS[p1];` +
      `[0:v]trim=${m.waitStart}:${m.waitEnd},setpts=(PTS-STARTPTS)/${k}[p2];` +
      `[0:v]trim=${m.waitEnd}:${m.end},setpts=PTS-STARTPTS[p3];` +
      `[p1][p2][p3]concat=n=3:v=1:a=0,scale=${W}:${H}:flags=lanczos,fps=30[base];` +
      `[base][2:v]overlay=0:0:enable='between(t,${a.toFixed(2)},${(a + fast).toFixed(2)})'[b2];`;
  } else {
    vchain = `[0:v]trim=${m.start}:${m.end},setpts=PTS-STARTPTS,scale=${W}:${H}:flags=lanczos,fps=30[b2];`;
  }
  sh("ffmpeg", ["-y", ...ffInputs, "-filter_complex", `${vchain}[b2][1:v]overlay=0:0,format=yuv420p[v]`, "-map", "[v]", "-an", "-c:v", "libx264", "-crf", "18", "-preset", "medium", segFile]);
  const vlen = dur(segFile);
  manifest.push({ id: s.id, file: path.basename(segFile), video_s: +vlen.toFixed(2), expected_voice_s: +s.audio.toFixed(1) });
  // silent preview: freeze the last frame until the expected narration has ended
  const total = Math.max(vlen, s.audio + 0.8);
  const pv = path.join(OUT, `${s.id}-preview.mp4`);
  sh("ffmpeg", ["-y", "-i", segFile, "-f", "lavfi", "-t", total.toFixed(2), "-i", "anullsrc=r=48000:cl=stereo", "-filter_complex",
    `[0:v]tpad=stop_mode=clone:stop_duration=${(total - vlen + 0.05).toFixed(2)},trim=0:${total.toFixed(2)},setpts=PTS-STARTPTS[v]`,
    "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-c:a", "aac", pv]);
  console.log(`segment ${s.id}: video ${vlen.toFixed(1)} s, expected voice ${s.audio.toFixed(1)} s`);
  preview.push(pv);
}
await browser.close();
fs.writeFileSync(path.join(SEGDIR, "manifest.json"), JSON.stringify(manifest, null, 2));
const list = path.join(OUT, "list.txt");
fs.writeFileSync(list, preview.map((p) => `file '${p}'`).join("\n"));
const final = path.join(VIDEO, "OutcomeGuard-silent-preview.mp4");
sh("ffmpeg", ["-y", "-f", "concat", "-safe", "0", "-i", list, "-c:v", "libx264", "-crf", "19", "-preset", "slow", "-c:a", "aac", "-movflags", "+faststart", final]);
console.log(`SILENT PREVIEW ${final} · ${dur(final).toFixed(1)} s — add the voice with mux-voice.mjs`);
