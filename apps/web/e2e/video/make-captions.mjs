// Captions (SRT) + YouTube chapters for the cinematic cut, from the real edit:
// segment offsets come from docs/hackathon/video/cinematic-build/*.mov, cue boundaries are
// snapped to the pauses detected in each voice file. Run after build-cinematic.mjs.
//   node e2e/video/make-captions.mjs
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { SEG } from "./segments.mjs";

const VIDEO = path.resolve(import.meta.dirname, "../../../../docs/hackathon/video");
const BUILD = path.join(VIDEO, "cinematic-build");
const SUB = path.resolve(VIDEO, "../submission");
const LEAD = 0.3;
const sh = (cmd, args) => execFileSync(cmd, args, { stdio: ["ignore", "pipe", "pipe"] }).toString();
const shErr = (cmd, args) => { try { return execFileSync(cmd, args, { stdio: ["ignore", "pipe", "pipe"] }).toString(); } catch (e) { return String(e.stderr); } };
const dur = (f) => Number(sh("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f]).trim());

// segment order of the edit → which voice file it carries
const ORDER = [
  ["01-intro", "s1", "Intro — paying for a promise"],
  ["02-concept", "s2", "How OutcomeGuard pays"],
  ["03-ch1", null, "AI drafts the agreement (live Claude)"],
  ["04-ai", "s3", null],
  ["05-ch2", null, "Tools verify the delivery (live Chromium + Lighthouse)"],
  ["06-verify", "s4", null],
  ["07-broken", "s5", "Broken delivery → FAIL (recorded earlier)"],
  ["08-ch3", null, "Humans approve · capture (simulated)"],
  ["09-capture", "s6", null],
  ["10-ch4", null, "Real PayPal Sandbox proof"],
  ["11-proof", "s7", null],
  ["12-outro", "s8", "Pay for proof"],
];
const text = Object.fromEntries(SEG.map((s) => [s.id, s.say]));

function pauses(file) {
  const out = shErr("ffmpeg", ["-i", file, "-af", "silencedetect=n=-38dB:d=0.12", "-f", "null", "-"]);
  const st = [...out.matchAll(/silence_start: ([0-9.]+)/g)].map((m) => +m[1]);
  const en = [...out.matchAll(/silence_end: ([0-9.]+)/g)].map((m) => +m[1]);
  return st.map((s, i) => [s, en[i] ?? s]);
}
function chunks(t) {
  const out = [];
  for (const s of t.split(/(?<=[.!?:])\s+/)) {
    if (s.length <= 84) { out.push(s); continue; }
    const commas = [...s.matchAll(/, /g)].map((m) => m.index);
    const cut = commas.sort((a, b) => Math.abs(a - s.length / 2) - Math.abs(b - s.length / 2))[0];
    if (cut == null) out.push(s);
    else out.push(s.slice(0, cut + 1), s.slice(cut + 2));
  }
  return out;
}
const ts = (t) => {
  const ms = Math.round(t * 1000), h = Math.floor(ms / 3600000), m = Math.floor(ms / 60000) % 60, s = Math.floor(ms / 1000) % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")},${String(ms % 1000).padStart(3, "0")}`;
};
const mmss = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;

let offset = 0;
const cues = [], chapters = [];
for (const [id, voice, chapter] of ORDER) {
  const len = dur(path.join(BUILD, `${id}.mov`));
  if (chapter) chapters.push(`${mmss(offset)} ${chapter}`);
  if (voice) {
    const file = path.join(VIDEO, "voice", `${voice}.mp3`);
    const vlen = dur(file);
    const sil = pauses(file);
    const s0 = sil[0] && sil[0][0] < 0.05 ? sil[0][1] : 0;
    const last = sil.at(-1);
    const s1 = last && last[1] >= vlen - 0.05 ? last[0] : vlen;
    const parts = chunks(text[voice]);
    const total = parts.reduce((a, p) => a + p.length, 0);
    const mids = sil.map(([a, b]) => (a + b) / 2).filter((m) => m > s0 + 0.3 && m < s1 - 0.3);
    let acc = 0;
    const bounds = [s0];
    for (const p of parts.slice(0, -1)) {
      acc += p.length;
      const guess = s0 + ((s1 - s0) * acc) / total;
      const near = mids.reduce((best, m) => (Math.abs(m - guess) < Math.abs(best - guess) ? m : best), Infinity);
      bounds.push(Math.abs(near - guess) < 1.2 && near > bounds.at(-1) + 0.6 ? near : guess);
    }
    bounds.push(s1);
    parts.forEach((p, i) => cues.push([offset + LEAD + bounds[i], offset + LEAD + bounds[i + 1] + (i === parts.length - 1 ? 0.3 : 0), p]));
  }
  offset += len;
}
const srt = cues.map(([a, b, t], i) => `${i + 1}\n${ts(a)} --> ${ts(b)}\n${t}\n`).join("\n");
fs.writeFileSync(path.join(SUB, "03b_captions.srt"), srt);
fs.writeFileSync(path.join(VIDEO, "chapters.txt"), chapters.join("\n") + "\n");
console.log(`captions: ${cues.length} cues · total ${offset.toFixed(1)} s\n${chapters.join("\n")}`);
