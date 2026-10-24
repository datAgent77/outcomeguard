// Adds the voice-over (e.g. ElevenLabs) to the silent segments made by build-video.mjs.
// Put one file per segment in docs/hackathon/video/voice/: s1.mp3 … s8.mp3 (.wav/.m4a also ok).
// Each segment lasts max(its video, LEAD + voice + TAIL); if the voice is longer, the last
// frame is held. Loudness is normalised to −16 LUFS (YouTube-friendly).
//   node e2e/video/mux-voice.mjs            → docs/hackathon/video/OutcomeGuard-demo.mp4
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const VIDEO = path.resolve(import.meta.dirname, "../../../../docs/hackathon/video");
const SEGDIR = path.join(VIDEO, "segments");
const VOICEDIR = process.env.VOICE_DIR ?? path.join(VIDEO, "voice");
const OUT = path.join(VIDEO, "build-mux");
const LEAD = Number(process.env.LEAD ?? 0.3);
const TAIL = Number(process.env.TAIL ?? 0.7);
const W = 1920, H = 1080;
const sh = (cmd, args) => execFileSync(cmd, args, { stdio: ["ignore", "pipe", "pipe"] }).toString();
const dur = (f) => Number(sh("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f]).trim());

const manifest = JSON.parse(fs.readFileSync(path.join(SEGDIR, "manifest.json"), "utf8"));
const voiceFile = (id) => ["mp3", "wav", "m4a"].map((e) => path.join(VOICEDIR, `${id}.${e}`)).find((f) => fs.existsSync(f));
const missing = manifest.filter((m) => !voiceFile(m.id)).map((m) => m.id);
if (missing.length) throw new Error(`missing voice files in ${VOICEDIR}: ${missing.join(", ")}`);
fs.rmSync(OUT, { recursive: true, force: true });
fs.mkdirSync(OUT, { recursive: true });

const parts = [];
let t = 0;
for (const m of manifest) {
  const voice = voiceFile(m.id);
  const v = dur(voice);
  const out = path.join(OUT, `${m.id}.mp4`);
  const audio = `[1:a]aresample=48000,aformat=channel_layouts=stereo,adelay=${LEAD * 1000}:all=1,apad`;
  if (m.card) {
    const total = LEAD + v + 1.5;
    sh("ffmpeg", ["-y", "-loop", "1", "-t", total.toFixed(2), "-i", path.join(SEGDIR, m.png), "-i", voice, "-filter_complex",
      `[0:v]scale=${W}:${H},fps=30,format=yuv420p,fade=t=in:st=0:d=0.4,fade=t=out:st=${(total - 0.6).toFixed(2)}:d=0.6[v];${audio},atrim=0:${total.toFixed(2)}[a]`,
      "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-c:a", "pcm_s16le", out.replace(".mp4", ".mov")]);
    parts.push(out.replace(".mp4", ".mov"));
    console.log(`${m.id}: card, voice ${v.toFixed(1)} s → ${total.toFixed(1)} s`);
    t += total;
    continue;
  }
  const seg = path.join(SEGDIR, m.file);
  const vlen = dur(seg);
  const total = Math.max(vlen, LEAD + v + TAIL);
  const hold = total - vlen;
  sh("ffmpeg", ["-y", "-i", seg, "-i", voice, "-filter_complex",
    `[0:v]tpad=stop_mode=clone:stop_duration=${(hold + 0.05).toFixed(2)},trim=0:${total.toFixed(2)},setpts=PTS-STARTPTS[v];${audio},atrim=0:${total.toFixed(2)}[a]`,
    "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-c:a", "pcm_s16le", out.replace(".mp4", ".mov")]);
  parts.push(out.replace(".mp4", ".mov"));
  const warn = hold > 4 ? `  ⚠ last frame held ${hold.toFixed(1)} s (voice longer than the footage)` : "";
  console.log(`${m.id}: starts ${t.toFixed(1)} s · video ${vlen.toFixed(1)} s · voice ${v.toFixed(1)} s → ${total.toFixed(1)} s${warn}`);
  t += total;
}
const list = path.join(OUT, "list.txt");
fs.writeFileSync(list, parts.map((p) => `file '${p}'`).join("\n"));
const final = path.join(VIDEO, "OutcomeGuard-demo.mp4");
sh("ffmpeg", ["-y", "-f", "concat", "-safe", "0", "-i", list, "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
  "-c:v", "libx264", "-crf", "19", "-preset", "slow", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", final]);
const total = dur(final);
console.log(`FINAL ${final} · ${total.toFixed(1)} s${total >= 179 ? "  ⚠ over the 3:00 limit" : ""}`);
