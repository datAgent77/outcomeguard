// Devpost gallery cards (1500×1000, 3:2) and a YouTube thumbnail (1280×720) made from REAL
// frames of the cinematic cut + the AG Grid ledger screenshot. Each card keeps its truth label.
//   node e2e/video/make-stills.mjs
import { chromium } from "@playwright/test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const VIDEO = path.resolve(import.meta.dirname, "../../../../docs/hackathon/video");
const SRC = path.join(VIDEO, "OutcomeGuard-cinematic.mp4");
const OUT = path.resolve(VIDEO, "../submission/gallery");
const TMP = path.join(VIDEO, "stills-tmp");
const LEDGER = path.resolve(VIDEO, "../screenshots/13-ledger-grid.png");
fs.mkdirSync(OUT, { recursive: true });
fs.mkdirSync(TMP, { recursive: true });
const frame = (t, name) => {
  const f = path.join(TMP, `${name}.png`);
  execFileSync("ffmpeg", ["-v", "error", "-y", "-ss", String(t), "-i", SRC, "-frames:v", "1", f]);
  return f;
};
const TONE = { live: "#13804f", recorded: "#33445f", simulated: "#b26a00", real: "#0f6b46", info: "#2952d4" };
const CARDS = [
  { t: 11.6, n: "01-pay-for-proof", title: "Don't just pay for a promise. Pay for proof.", tag: ["Concept", "info"] },
  { t: 25.6, n: "02-who-does-what", title: "AI drafts · Tools verify · Humans approve · PayPal executes", tag: ["How it works", "info"] },
  { t: 39.5, n: "03-claude-criteria", title: "Claude turns a plain-English request into measurable, buyer-quoted criteria", tag: ["LIVE · Claude", "live"] },
  { t: 51.5, n: "04-lock-hash", title: "The buyer locks the agreement — a content hash freezes the goalposts", tag: ["LIVE", "live"] },
  { t: 72.0, n: "05-evidence-report", title: "Real Chromium + Lighthouse evidence: 3 passed, 1 waits for a human", tag: ["LIVE · real tools", "live"] },
  { t: 86.2, n: "06-broken-fail", title: "A broken delivery fails — and the payment stays blocked", tag: ["Recorded earlier", "recorded"] },
  { t: 108.5, n: "07-capture-simulated", title: "Policy re-checked, buyer approves, capture — clearly labelled simulated", tag: ["SIMULATED · rehearsal mode", "simulated"] },
  { t: 128.0, n: "08-sandbox-proof", title: "Real PayPal Sandbox: governed capture, two-party refund, void", tag: ["Real PayPal Sandbox · Oct 16, 2026", "real"] },
  { img: LEDGER, n: "09-ag-grid-ledger", title: "Payment & evidence ledger built with AG Grid Community", tag: ["Rehearsal data", "simulated"] },
];

const browser = await chromium.launch();
async function render(file, w, h, html) {
  const p = await browser.newPage({ viewport: { width: w, height: h } });
  await p.setContent(html, { waitUntil: "load" });
  await p.screenshot({ path: file, type: "jpeg", quality: 90 });
  await p.close();
}
const FONT = `-apple-system,'SF Pro Display','Helvetica Neue',Arial,sans-serif`;
for (const c of CARDS) {
  const img = c.img ?? frame(c.t, c.n);
  const data = `data:image/png;base64,${fs.readFileSync(img).toString("base64")}`;
  await render(path.join(OUT, `${c.n}.jpg`), 1500, 1000, `<!doctype html><html><body style="margin:0;width:1500px;height:1000px;background:radial-gradient(1200px 700px at 50% 30%,#18366a,#0b1830 60%,#060d1c);font-family:${FONT};color:#fff;overflow:hidden">
    <div style="height:150px;display:flex;align-items:center;gap:22px;padding:0 44px">
      <span style="flex:none;padding:10px 18px;border-radius:999px;background:${TONE[c.tag[1]]};font-weight:700;font-size:20px;letter-spacing:.05em">${c.tag[0]}</span>
      <span style="font-size:33px;font-weight:700;line-height:1.2;letter-spacing:-.01em">${c.title}</span>
    </div>
    <div style="display:flex;justify-content:center"><img src="${data}" style="width:1412px;height:${c.img ? "auto" : "794px"};max-height:806px;object-fit:contain;border-radius:16px;box-shadow:0 30px 60px rgba(0,0,0,.45)"></div>
  </body></html>`);
  console.log(`gallery/${c.n}.jpg`);
}
// YouTube thumbnail (custom, readable at small size)
await render(path.join(OUT, "youtube-thumbnail.jpg"), 1280, 720, `<!doctype html><html><body style="margin:0;width:1280px;height:720px;background:radial-gradient(900px 600px at 30% 40%,#1d3f7a,#0b1830 60%,#060d1c);font-family:${FONT};color:#fff;overflow:hidden;position:relative">
  <div style="position:absolute;left:70px;top:120px;font-size:30px;font-weight:700;letter-spacing:.2em;color:#9db4f7">OUTCOMEGUARD</div>
  <div style="position:absolute;left:66px;top:170px;font-size:84px;font-weight:800;line-height:1.02;letter-spacing:-.03em">Don't pay for<br>a promise.<br><span style="color:#9db4f7">Pay for proof.</span></div>
  <div style="position:absolute;left:70px;top:560px;font-size:28px;color:#c3cad6">PayPal Sandbox · Claude · real-browser verification</div>
  <div style="position:absolute;right:70px;top:110px;width:440px;border-radius:26px;background:#fff;color:#0f1f3a;padding:30px 32px;box-shadow:0 40px 80px rgba(0,0,0,.5);transform:rotate(2deg)">
    <div style="font-size:18px;letter-spacing:.2em;color:#6b7a93;font-weight:700">THE PROOF</div>
    <div style="font-size:36px;font-weight:800;margin:4px 0 14px">$500 · captured</div>
    ${["Fits a phone screen", "Contact form works", "Lighthouse ≥ 90", "Buyer approves"].map((t) => `<div style="display:flex;align-items:center;gap:14px;font-size:25px;font-weight:650;padding:11px 0;border-bottom:1px solid #e8edf5"><span style="width:34px;height:34px;border-radius:50%;background:#13804f;color:#fff;display:flex;align-items:center;justify-content:center;font-size:20px">✓</span>${t}</div>`).join("")}
  </div>
</body></html>`);
console.log("gallery/youtube-thumbnail.jpg");
await browser.close();
fs.rmSync(TMP, { recursive: true, force: true });
