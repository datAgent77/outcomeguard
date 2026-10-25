# Voice-over for ElevenLabs — 8 files

▶ **Final video with this narration:** https://youtu.be/NC4gV5sbMEg

The video is already recorded as silent, captioned segments
(`docs/hackathon/video/segments/`, preview `OutcomeGuard-silent-preview.mp4`). Generate **one
audio file per segment** with the exact text below, save them as `s1.mp3` … `s8.mp3` in
`docs/hackathon/video/voice/`, then run the mux script (bottom). Timing is automatic: each
segment stretches to fit its voice file (it holds the last frame if the voice is longer), so
you don't need to hit the target length exactly. Total must stay under 3:00 (expect ≈ 2:10).

## Suggested settings
- One voice for all 8 files: a calm, confident English narrator (product demo / explainer tone).
- Moderate stability and a natural pace (speed ≈ 1.0). Keep the same settings for every file.
- Output: MP3, 44.1 kHz, 128 kbps or higher (WAV also works).
- Listen for: "OutcomeGuard" (Outcome-Guard), "Chromium" (KROH-mee-um), "Lighthouse",
  "PayPal". If a word sounds wrong, regenerate that file only.

## Files

| File | Target | On screen |
|---|---|---|
| `s1.mp3` | ~10 s | Landing page hero |
| `s2.mp3` | ~12 s | Landing: proof cards, "How it works" |
| `s3.mp3` | ~27 s | **LIVE** Claude drafts criteria → buyer configures and locks |
| `s4.mp3` | ~22 s | **LIVE** Chromium + Lighthouse verification → evidence report |
| `s5.mp3` | ~9 s | **RECORDED EARLIER** broken delivery, contact form FAIL |
| `s6.mp3` | ~17 s | **SIMULATED** policy eligible → buyer approves → capture |
| `s7.mp3` | ~15 s | **REAL PAYPAL SANDBOX** proof cards (Oct 16, 2026) |
| `s8.mp3` | ~4 s | Closing card |

### s1.mp3
Hiring a freelancer means paying for a promise. Either the buyer pays first and hopes, or the freelancer works first and hopes. OutcomeGuard replaces hope with proof.

### s2.mp3
The payment is authorized on PayPal up front, but it is captured only when measurable evidence, and the buyer, say yes. AI drafts, tools verify, humans approve, PayPal executes.

### s3.mp3
Acme needs a five-hundred-dollar landing page. The buyer writes the request in plain English, and Claude turns it into measurable acceptance criteria. Each one quotes the buyer's own words. Anything that says "must" stays blocking, and the AI's own idea is only a suggestion, off by default. The buyer adds what only they know, and locks the agreement. Locking freezes a content hash, so nobody can quietly move the goalposts.

### s4.mp3
Now the freelancer delivers. OutcomeGuard opens the page in a real Chromium browser on a phone-sized screen, submits the contact form to a controlled test endpoint, and runs Google Lighthouse. Three checks pass, in plain language, with stored screenshots and the Lighthouse report. Accessibility can't be measured by a tool, so it waits for a human.

### s5.mp3
Here is the first delivery, recorded earlier. The form was submitted, but the success message never appeared. That single failed check blocked the payment.

### s6.mp3
The accessibility decision is recorded, and labelled in the app as a scripted demo decision. The payment policy is re-evaluated: eligible. The buyer approves, then captures. Two separate, explicit steps. In this recording the payment is simulated, and the app says so.

### s7.mp3
And it isn't only a simulation. On PayPal Sandbox, we captured a five-hundred-dollar payment through this exact governance, refunded one hundred after both parties approved, and cancelled an authorization before capture. Every ID was read back from PayPal.

### s8.mp3
OutcomeGuard. Don't just pay for a promise. Pay for proof.

## Don't change the meaning
The wording is tied to the on-screen truth labels. If you edit, keep: "recorded earlier" (s5),
"scripted demo decision" and "simulated" (s6), "PayPal Sandbox" (s7). Don't say "real payment",
"escrow" or "human reviewer approved". If you change any text, update `apps/web/e2e/video/segments.mjs`
and `03b_captions.srt` to match.

## Merge
```bash
cd ~/outcomeguard/apps/web && node e2e/video/mux-voice.mjs
```
Output: `docs/hackathon/video/OutcomeGuard-demo.mp4` (1080p, loudness −16 LUFS). The script
prints each segment's start time and total length, and warns if it goes over 3:00 or holds a frame
for more than 4 s.
