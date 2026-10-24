// Cinematic cut of the product video. Same truth rules as build-video.mjs (LIVE Claude and
// Chromium/Lighthouse, RECORDED EARLIER broken run, SIMULATED capture, REAL Sandbox IDs), plus:
//  · motion-graphics intro / concept / chapter cards / outro (motion.html, synced to the voice)
//  · a "camera" inside the real app: smooth zoom + spotlight on the element being narrated
//    (done in the browser, so text stays sharp), timed to the voice files' sentence starts
//  · fades between scenes. Uses the ElevenLabs files in docs/hackathon/video/voice/s1..s8.mp3.
//   BASE=http://localhost:3010 READY=<prepare_demo --ready id> DEMO_PASSWORD=… node e2e/video/build-cinematic.mjs
import { chromium } from "@playwright/test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { SEG } from "./segments.mjs";

const BASE = process.env.BASE ?? "http://localhost:3010";
const API = process.env.API ?? "http://localhost:8010";
const READY = process.env.READY;
const PW = process.env.DEMO_PASSWORD;
if (!READY || !PW) throw new Error("READY and DEMO_PASSWORD required");
const VIDEO = path.resolve(import.meta.dirname, "../../../../docs/hackathon/video");
const OUT = path.join(VIDEO, "cinematic-build");
const VOICEDIR = path.join(VIDEO, "voice");
const MOTION = "file://" + path.join(import.meta.dirname, "motion.html");
fs.rmSync(OUT, { recursive: true, force: true });
fs.mkdirSync(OUT, { recursive: true });
const W = 1920, H = 1080, VW = 1280, VH = 720, LEAD = 0.3, TAIL = 0.7, NAVY = "0x0b1830";
const sh = (cmd, args) => execFileSync(cmd, args, { stdio: ["ignore", "pipe", "pipe"] }).toString();
const dur = (f) => Number(sh("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f]).trim());
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const S = Object.fromEntries(SEG.map((s) => [s.id, s]));
for (const s of SEG) {
  s.voice = path.join(VOICEDIR, `${s.id}.mp3`);
  if (!fs.existsSync(s.voice)) throw new Error(`missing ${s.voice}`);
  s.vdur = dur(s.voice);
}

const browser = await chromium.launch();
const COLORS = { live: "#13804f", recorded: "#33445f", simulated: "#b26a00", real: "#0f6b46" };
async function overlay(file, { badge, caption, extra }) {
  const p = await browser.newPage({ viewport: { width: W, height: H } });
  await p.setContent(`<!doctype html><html><head><style>
    html,body{margin:0;background:transparent;font-family:-apple-system,"SF Pro Display","Helvetica Neue",Arial,sans-serif}
    .badge{position:absolute;left:48px;top:36px;padding:12px 22px;border-radius:999px;color:#fff;font-weight:700;font-size:28px;letter-spacing:.06em;box-shadow:0 6px 20px rgba(0,0,0,.25)}
    .cap{position:absolute;left:50%;bottom:54px;transform:translateX(-50%);max-width:1640px;padding:20px 34px;border-radius:18px;background:rgba(10,22,40,.88);color:#fff;font-size:36px;font-weight:600;line-height:1.3;text-align:center;box-shadow:0 10px 30px rgba(0,0,0,.3)}
    .extra{position:absolute;right:48px;top:36px;padding:12px 22px;border-radius:14px;background:rgba(41,82,212,.95);color:#fff;font-size:28px;font-weight:700}
  </style></head><body>
    ${badge && badge[0] ? `<div class="badge" style="background:${COLORS[badge[1]]}">${badge[0]}</div>` : ""}
    ${caption ? `<div class="cap">${caption}</div>` : ""}
    ${extra ? `<div class="extra">${extra}</div>` : ""}
  </body></html>`);
  await p.screenshot({ path: file, omitBackground: true });
  await p.close();
}

// ------------------------------------------------------------------ camera (runs in the page)
const CAMERA = () => {
  window.__cam = (() => {
    let spot = null, lock = null;
    const EASE = "cubic-bezier(.65,0,.35,1)";
    // while zoomed, the browser must not auto-scroll (e.g. to show a caret) — that would move the shot
    addEventListener("scroll", () => { if (lock && (scrollX !== lock[0] || scrollY !== lock[1])) scrollTo(lock[0], lock[1]); });
    function measure(el) {
      const b = document.body, pt = b.style.transform, ptr = b.style.transition;
      b.style.transition = "none";
      b.style.transform = "none";
      const r = el.getBoundingClientRect();
      const o = { x: r.left + scrollX, y: r.top + scrollY, w: r.width, h: r.height };
      b.style.transform = pt;
      void b.offsetHeight;
      b.style.transition = ptr;
      return o;
    }
    function focus(el, { zoom, maxW, pad = 14, dim = 0.5, ms = 1000, y = 0.45 } = {}) {
      // room below the page so elements near the bottom can still be centred above the caption
      if (!document.body.dataset.camPad) { document.body.dataset.camPad = "1"; document.body.style.paddingBottom = "60vh"; }
      const r = measure(el), VW = innerWidth, VH = innerHeight;
      if (maxW && r.w > maxW) r.w = maxW; // focus on the left part of a very wide element
      const fit = Math.min((VW * 0.9) / (r.w + 2 * pad), (VH * 0.74) / (r.h + 2 * pad));
      const s = Math.max(1, Math.min(zoom ?? 2.3, fit));
      const cx = r.x + r.w / 2, cy = r.y + r.h / 2;
      const docW = document.documentElement.scrollWidth, docH = document.documentElement.scrollHeight;
      let tx = VW / 2 + scrollX - cx * s, ty = VH * y + scrollY - cy * s;
      tx = Math.min(scrollX, Math.max(tx, scrollX + VW - docW * s));
      ty = Math.min(scrollY, Math.max(ty, scrollY + VH - docH * s));
      lock = [scrollX, scrollY];
      // a sticky header would otherwise float over the zoomed content (sticky -> static keeps the layout)
      document.querySelectorAll("header").forEach((h) => { if (getComputedStyle(h).position === "sticky") h.style.position = "static"; });
      const b = document.body;
      b.style.transformOrigin = "0 0";
      b.style.transition = `transform ${ms}ms ${EASE}`;
      b.style.transform = `translate(${tx}px, ${ty}px) scale(${s})`;
      if (!spot || !spot.isConnected) {
        spot = document.createElement("div");
        spot.style.cssText = `position:absolute;z-index:2147483000;pointer-events:none;border-radius:14px;opacity:0;`;
        b.appendChild(spot);
        void spot.offsetHeight;
      }
      spot.style.transition = `all ${ms}ms ${EASE}`;
      Object.assign(spot.style, {
        left: r.x - pad + "px", top: r.y - pad + "px", width: r.w + 2 * pad + "px", height: r.h + 2 * pad + "px", opacity: "1",
        boxShadow: `0 0 0 3px rgba(58,100,230,.95), 0 0 0 4000px rgba(10,22,40,${dim})`,
      });
    }
    function reset(ms = 900) {
      const b = document.body;
      b.style.transition = `transform ${ms}ms ${EASE}`;
      b.style.transform = "translate(0px, 0px) scale(1)";
      if (spot) { spot.style.transition = `opacity ${ms}ms ${EASE}`; spot.style.opacity = "0"; }
      setTimeout(() => { if (b.style.transform === "translate(0px, 0px) scale(1)") { b.style.transition = ""; b.style.transform = ""; lock = null; } }, ms + 50);
    }
    function zoomSelf(el, s = 1.35, ms = 900) {
      el.style.transition = `transform ${ms}ms ${EASE}`;
      el.style.transform = `scale(${s})`;
    }
    return { focus, reset, zoomSelf };
  })();
};

// ------------------------------------------------------------------ recorders
async function motion(item, scene, opts, seconds) {
  const ctx = await browser.newContext({ viewport: { width: W, height: H }, recordVideo: { dir: OUT, size: { width: W, height: H } } });
  const page = await ctx.newPage();
  const t0 = Date.now();
  await page.goto(MOTION);
  await sleep(500);
  const start = (Date.now() - t0) / 1000;
  await page.evaluate(([n, o]) => window.play(n, o), [scene, opts]);
  await sleep(seconds * 1000 + 300);
  const v = page.video();
  await ctx.close();
  // the scene timeline is voice time; begin LEAD s earlier on the empty stage
  Object.assign(item, { raw: await v.path(), marks: { start: Math.max(0, start - LEAD), end: start + seconds }, len: seconds + LEAD });
}

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

async function take(item, seg, who, url, actions) {
  const ctx = await browser.newContext({
    baseURL: BASE, viewport: { width: VW, height: VH }, deviceScaleFactor: 1.5,
    storageState: who ? ST[who] : undefined, recordVideo: { dir: OUT, size: { width: VW, height: VH } },
  });
  await ctx.addInitScript(CAMERA);
  const page = await ctx.newPage();
  const t0 = Date.now();
  const rel = () => (Date.now() - t0) / 1000;
  await page.goto(url);
  await page.waitForLoadState("networkidle").catch(() => {});
  await sleep(600);
  const m = { start: rel() };
  let comp = 0;
  // wait until voice-time t (seconds into this segment's narration) in the FINAL edit
  const at = async (t) => { while (rel() - comp < m.start + LEAD + t) await sleep(25); };
  const wait = async (fn) => {
    m.waitStart = rel();
    await fn();
    m.waitEnd = rel();
    const real = m.waitEnd - m.waitStart;
    m.k = Math.max(1, real / 3.5);
    comp = real - real / m.k;
  };
  const cam = {
    focus: async (loc, o = {}) => { const h = await loc.first().elementHandle({ timeout: 20000 }); await page.evaluate(([el, o]) => window.__cam.focus(el, o), [h, o]); },
    reset: (ms) => page.evaluate((ms) => window.__cam.reset(ms), ms),
    zoomSelf: async (loc, s) => { const h = await loc.first().elementHandle(); await page.evaluate(([el, s]) => window.__cam.zoomSelf(el, s), [h, s]); },
  };
  await actions(page, { at, wait, cam, sleep });
  await at(seg.vdur + TAIL);
  m.end = rel();
  const v = page.video();
  await ctx.close();
  Object.assign(item, { raw: await v.path(), marks: m, len: m.end - m.start - comp });
  console.log(`recorded ${item.id}: ${JSON.stringify(m)}`);
}

// ------------------------------------------------------------------ the edit
const items = [];
const add = (o) => (items.push(o), o);
const intro = add({ id: "01-intro", seg: S.s1 });
const concept = add({ id: "02-concept", seg: S.s2 });
const ch1 = add({ id: "03-ch1", chapter: { active: 0, title: "AI drafts the agreement", sub: "Live · Claude" } });
const ai = add({ id: "04-ai", seg: S.s3, app: true });
const ch2 = add({ id: "05-ch2", chapter: { active: 1, title: "Tools verify the delivery", sub: "Live · real Chromium + Google Lighthouse" } });
const ver = add({ id: "06-verify", seg: S.s4, app: true });
const brk = add({ id: "07-broken", seg: S.s5, app: true });
const ch3 = add({ id: "08-ch3", chapter: { active: 2, title: "Humans approve · PayPal executes", sub: "Rehearsal mode · this payment is simulated" } });
const cap = add({ id: "09-capture", seg: S.s6, app: true });
const ch4 = add({ id: "10-ch4", chapter: { active: -1, title: "Proven on PayPal Sandbox", sub: "Real Sandbox runs · recorded Oct 16, 2026" } });
const proof = add({ id: "11-proof", seg: S.s7, app: true });
const outro = add({ id: "12-outro", seg: S.s8 });

await motion(intro, "intro", {}, S.s1.vdur + TAIL);
await motion(concept, "concept", {}, S.s2.vdur + TAIL);
for (const c of [ch1, ch2, ch3, ch4]) await motion(c, "chapter", c.chapter, 2.2);
await motion(outro, "outro", {}, S.s8.vdur + 1.6);

let newAgreement = "";
await take(ai, S.s3, "alice", "/agreements/new", async (page, { at, wait, cam, sleep }) => {
  await at(0);
  await page.getByRole("button", { name: "Use the example request" }).click();
  await at(0.8);
  await cam.focus(page.locator("#req"), { zoom: 1.7 });
  await at(5.0);
  await cam.reset(500);
  await sleep(450);
  await page.getByRole("button", { name: "Generate Acceptance Criteria" }).click();
  await wait(() => page.getByText("Requirements from your request").waitFor({ timeout: 120000 }));
  await sleep(300);
  const first = page.locator('[data-testid^="criterion-"]').first();
  await at(8.4);
  await cam.focus(first);
  await at(11.0);
  await cam.focus(first.getByText("Blocking").first().locator(".."), { zoom: 2.1 });
  await at(13.7);
  await cam.focus(page.locator('[data-testid^="criterion-"]', { hasText: "AI-suggested" }));
  await at(17.6);
  await cam.reset(500);
  await sleep(500);
  await page.getByRole("button", { name: "Continue to configuration" }).click();
  for (const cb of await page.getByRole("checkbox", { name: /Require HTTPS/ }).all()) await cb.click();
  for (const b of await page.getByRole("button", { name: "Use the standard field set" }).all()) await b.click();
  for (const sc of await page.getByLabel("Minimum performance score (0–100)").all()) if (!(await sc.inputValue())) await sc.fill("90");
  await page.getByLabel("Title").pressSequentially("Spring campaign landing page", { delay: 25 });
  await page.getByLabel("Delivery deadline").fill(new Date(Date.now() + 14 * 86400000).toISOString().slice(0, 10));
  await sleep(400);
  await page.getByRole("button", { name: "Create draft agreement" }).click();
  await page.getByRole("button", { name: "Propose this version" }).click();
  await sleep(300);
  await page.getByRole("button", { name: "Approve and lock…" }).click();
  const dlg = page.getByRole("dialog");
  await dlg.waitFor();
  await sleep(250);
  await cam.zoomSelf(dlg, 1.45);
  await at(24.6);
  await cam.zoomSelf(dlg, 1);
  await sleep(500);
  await dlg.getByRole("button", { name: "Approve and lock" }).click();
  await page.getByText("Locked", { exact: true }).first().waitFor();
  newAgreement = new URL(await page.getByRole("link", { name: /Open agreement workspace/ }).getAttribute("href"), BASE).pathname;
  await sleep(300);
  await cam.focus(page.getByText("Locked", { exact: true }).first().locator("xpath=ancestor::section[1]"), { dim: 0.35 });
});

await take(ver, S.s4, "maya", `${newAgreement}?tab=delivery`, async (page, { at, wait, cam, sleep }) => {
  await at(0);
  await cam.focus(page.getByLabel("Delivery URL"), { maxW: 520, zoom: 1.6, ms: 800 });
  await page.getByLabel("Delivery URL").click();
  await page.getByLabel("Delivery URL").pressSequentially("http://127.0.0.1:8020/landing-page-fixed/", { delay: 22 });
  await at(1.9);
  await cam.reset(450);
  await sleep(450);
  await page.getByRole("button", { name: "Submit evidence for verification" }).click();
  await wait(() => page.getByRole("button", { name: "Open report" }).first().waitFor({ timeout: 180000 }));
  await page.getByRole("button", { name: "Open report" }).first().click();
  await page.getByRole("heading", { name: "Needs human review" }).waitFor();
  await sleep(400);
  const res = (re) => page.locator('[data-testid^="result-"]').filter({ hasText: re }).filter({ visible: true });
  await at(6.8);
  await cam.focus(res(/contact form/i));
  await at(9.85);
  await cam.focus(res(/Lighthouse performance/i));
  await at(11.7);
  await cam.focus(page.getByText("Verification Report", { exact: true }).locator("xpath=ancestor::div[contains(@class,'border-b')][1]"));
  await at(14.2);
  await cam.focus(res(/phone/i));
  await at(17.0);
  await cam.focus(res(/accessible/i));
});

await take(brk, S.s5, "alice", `/agreements/${READY}?tab=evidence&run=${brokenRun}`, async (page, { at, cam }) => {
  await page.getByText("Verification Report", { exact: true }).waitFor();
  await at(0);
  await cam.focus(page.getByText("Verification Report", { exact: true }).locator("xpath=ancestor::div[contains(@class,'border-b')][1]"));
  await at(2.9);
  const card = page.getByTestId("result-contact_form_works");
  await cam.focus(card);
  await at(4.3);
  await cam.focus(card.getByText(/never appeared/).filter({ visible: true }), { maxW: 760 });
  await at(6.7);
  await cam.focus(card, { dim: 0.6 });
});

await take(cap, S.s6, "bob", `/agreements/${READY}?tab=evidence`, async (page, { at, cam, sleep }) => {
  await page.getByTestId("manual-review-panel").first().waitFor();
  await at(0);
  await cam.focus(page.getByTestId("manual-review-panel").first());
  await at(5.1);
  await cam.reset(500);
  await sleep(500);
  await page.getByRole("tab", { name: "Payment" }).click();
  await page.getByText("Eligible for buyer approval").first().waitFor({ timeout: 30000 });
  await sleep(300);
  await cam.focus(page.locator("section").filter({ hasText: "Payment policy" }).last());
  await at(8.6);
  await cam.reset(450);
  await sleep(450);
  await page.getByRole("button", { name: "Approve capture…" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "I approve the capture" }).click();
  await page.getByText("Capture approved by the buyer").waitFor();
  await sleep(300);
  await page.getByRole("button", { name: /Capture \$500\.00 via PayPal/ }).click();
  const dlg = page.getByRole("dialog").filter({ hasText: "Capture payment" });
  await dlg.waitFor();
  await cam.zoomSelf(dlg, 1.4);
  await at(12.6);
  await cam.zoomSelf(dlg, 1);
  await sleep(350);
  await dlg.getByRole("button", { name: "Capture payment" }).click();
  const done = page.getByText("Payment captured (SIMULATED)");
  await done.waitFor({ timeout: 30000 });
  await sleep(300);
  await at(13.5);
  await cam.focus(done.locator("xpath=ancestor::*[contains(@class,'border-pass-100')][1]"), { zoom: 2 });
});

await take(proof, S.s7, null, "/", async (page, { at, cam }) => {
  const sec = page.locator("section[aria-labelledby=proof-h] > div");
  const card = (t) => page.getByRole("heading", { name: t }).locator("xpath=ancestor::div[contains(@class,'rounded-xl')][1]");
  await sec.waitFor();
  await at(0);
  await cam.focus(sec, { dim: 0.35 });
  await at(2.4);
  await cam.focus(card("Proof-governed capture"));
  await at(7.4);
  await cam.focus(card("Two-party refund"));
  await at(10.1);
  await cam.focus(card("Cancelled before capture"));
  await at(13.0);
  await cam.focus(sec, { dim: 0.35 });
});

// ------------------------------------------------------------------ render
const parts = [];
for (const it of items) {
  const out = path.join(OUT, `${it.id}.mov`);
  const m = it.marks;
  const inputs = ["-i", it.raw];
  let chain;
  if (m.waitStart != null) {
    const real = m.waitEnd - m.waitStart, fast = real / m.k, a = m.waitStart - m.start;
    const note = path.join(OUT, `${it.id}-note.png`);
    await overlay(note, { extra: `⏩ sped up ${m.k.toFixed(1)}× · real wait ${real.toFixed(0)} s` });
    inputs.push("-i", note);
    chain =
      `[0:v]trim=${m.start}:${m.waitStart},setpts=PTS-STARTPTS[p1];[0:v]trim=${m.waitStart}:${m.waitEnd},setpts=(PTS-STARTPTS)/${m.k}[p2];` +
      `[0:v]trim=${m.waitEnd}:${m.end},setpts=PTS-STARTPTS[p3];[p1][p2][p3]concat=n=3:v=1:a=0,scale=${W}:${H}:flags=lanczos,fps=30[b0];` +
      `[b0][1:v]overlay=0:0:enable='between(t,${a.toFixed(2)},${(a + fast).toFixed(2)})'[b1];`;
  } else {
    chain = `[0:v]trim=${m.start}:${m.end},setpts=PTS-STARTPTS,scale=${W}:${H}:flags=lanczos,fps=30[b1];`;
  }
  if (it.app) {
    const capPng = path.join(OUT, `${it.id}-cap.png`);
    await overlay(capPng, { badge: it.seg.badge, caption: it.seg.caption });
    inputs.push("-i", capPng);
    chain += `[b1][${inputs.filter((x) => x === "-i").length - 1}:v]overlay=0:0[b2];`;
  } else chain += `[b1]null[b2];`;
  const vlen = it.len;
  const total = it.seg ? Math.max(vlen, LEAD + it.seg.vdur + TAIL) : vlen;
  const ai = inputs.filter((x) => x === "-i").length;
  if (it.seg) inputs.push("-i", it.seg.voice);
  else inputs.push("-f", "lavfi", "-t", total.toFixed(2), "-i", "anullsrc=r=48000:cl=stereo");
  chain +=
    `[b2]tpad=stop_mode=clone:stop_duration=${(total - vlen + 0.1).toFixed(2)},trim=0:${total.toFixed(2)},setpts=PTS-STARTPTS,` +
    `fade=t=in:st=0:d=0.3:color=${NAVY},fade=t=out:st=${(total - 0.3).toFixed(2)}:d=0.3:color=${NAVY},format=yuv420p[v];` +
    `[${ai}:a]aresample=48000,aformat=channel_layouts=stereo,${it.seg ? `adelay=${LEAD * 1000}:all=1,` : ""}apad,atrim=0:${total.toFixed(2)}[a]`;
  sh("ffmpeg", ["-y", ...inputs, "-filter_complex", chain, "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "17", "-preset", "medium", "-c:a", "pcm_s16le", out]);
  console.log(`${it.id}: ${total.toFixed(1)} s`);
  parts.push(out);
}
await browser.close();
const list = path.join(OUT, "list.txt");
fs.writeFileSync(list, parts.map((p) => `file '${p}'`).join("\n"));
const final = path.join(VIDEO, "OutcomeGuard-cinematic.mp4");
sh("ffmpeg", ["-y", "-f", "concat", "-safe", "0", "-i", list, "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
  "-c:v", "libx264", "-crf", "19", "-preset", "slow", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", final]);
const T = dur(final);
console.log(`FINAL ${final} · ${T.toFixed(1)} s${T >= 179 ? "  ⚠ over 3:00" : ""}`);
