# Three-minute video package

Target **2:50** (rules: under 3:00; judges need not watch beyond). **Final cut: 2:16** — `OutcomeGuard-cinematic.mp4`, built by `apps/web/e2e/video/build-cinematic.mjs` with the narration in `03c_VOICEOVER_ELEVENLABS.md`. One story, one voice, no
music (the rules forbid copyrighted music without permission). English narration.

## Truth labels (must stay visible / spoken)

| What viewers see | Truth | How it is signalled |
|---|---|---|
| Claude generating criteria | **LIVE** in the recording, real model call | caption "LIVE · Claude" |
| Chromium + Lighthouse verifying the fixed page | **LIVE**, real tools | caption "LIVE · real Chromium + Lighthouse"; if trimmed: "sped up 2×" |
| Broken page FAIL | **RECORDED earlier** (same app, prepared agreement) | caption "Recorded earlier" + narration |
| Capture on stage | **SIMULATED** (rehearsal mode) | orange REHEARSAL banner + "Payment captured (SIMULATED)" + caption |
| Capture / refund / void IDs | **REAL PayPal Sandbox**, Oct 16 2026 | caption "Real PayPal Sandbox · recorded Oct 16" |
| Accessibility review | **Scripted demo decision** | UI label; caption "scripted demo decision" |

Never show PayPal's own checkout page or logos (trademark rule; confirm if you want them).

## Narration script (291 words ≈ 2:00 spoken at 145 wpm, 2:14 at 130 wpm; the rest of the 2:50 is on-screen time)

**[0:00–0:15] Hook**
"Hiring a freelancer means paying for a promise. Either the buyer pays first and hopes, or
the freelancer works first and hopes. OutcomeGuard replaces hope with proof."

**[0:15–0:30] The idea**
"The payment is authorized on PayPal up front — but it is captured only when measurable
evidence, and the buyer, say yes. AI drafts, tools verify, humans approve, PayPal executes."

**[0:30–0:58] AI criteria (live)**
"Acme needs a five-hundred-dollar landing page. The buyer writes the request in plain
English. Claude turns it into measurable acceptance criteria — and each one quotes the
buyer's own words. Anything that says 'must' stays blocking. The AI's own idea is only a
suggestion, off by default."

**[0:58–1:10] Lock**
"The buyer adds what only they know and locks the agreement. Locking freezes a content
hash, so nobody can quietly move the goalposts."

**[1:10–1:40] Verification (live)**
"Now the freelancer delivers. OutcomeGuard opens the page in a real Chromium browser on a
phone-sized screen, submits the contact form to a controlled test endpoint, and runs Google
Lighthouse. Three checks pass — in plain language, with stored screenshots and the
Lighthouse report. Accessibility can't be measured by a tool, so it waits for a human."

**[1:40–1:55] Broken delivery (recorded)**
"Here is the first delivery, recorded earlier: the form was submitted, but the success
message never appeared. That single failed check blocked the payment."

**[1:55–2:25] Policy and capture (simulated)**
"An authorized reviewer records the accessibility decision. The payment policy is
re-evaluated: eligible. The buyer approves — and captures. Two separate, explicit steps.
In this recording the payment is simulated, and the app says so."

**[2:25–2:45] Real Sandbox proof**
"And it isn't only a simulation. On PayPal Sandbox we captured a five-hundred-dollar
payment through this exact governance, refunded one hundred after both parties approved,
and cancelled an authorization before capture — every ID read back from PayPal."

**[2:45–2:50] Close**
"OutcomeGuard. Don't just pay for a promise. Pay for proof."

## Storyboard (second by second)

| Time | Screen | Action | Caption (on screen) |
|---|---|---|---|
| 0:00–0:08 | Landing hero | slow zoom on headline | **Don't just pay for a promise. Pay for proof.** |
| 0:08–0:15 | Landing "How it works" | scroll | Freelance payments today = trust |
| 0:15–0:30 | Landing proof cards → back to hero | scroll | AI drafts · Tools verify · Humans approve · PayPal executes |
| 0:30–0:34 | Wizard (alice) | click "Use the example request" | Buyer: plain-English request |
| 0:34–0:50 | Wizard | Generate → criteria appear | **LIVE · Claude drafts acceptance criteria** (trim the wait to ~4 s with a visible "⏩ 15 s" mark) |
| 0:50–0:58 | Criteria cards | hover "Buyer-stated", "Blocking", suggestion unchecked | Every criterion quotes the buyer · AI suggestions are opt-in |
| 0:58–1:10 | Configure → Approve and lock dialog | confirm | Locked · content hash frozen |
| 1:10–1:18 | Delivery tab (maya) | paste fixed URL, submit | Freelancer delivers |
| 1:18–1:30 | "Verification running" → report | wait (trim to ~6 s, mark "⏩ 14 s") | **LIVE · real Chromium + Lighthouse** |
| 1:30–1:40 | Evidence report | scroll the 3 PASS cards + pending review + legend | Measured · AI interpretation · Human decision |
| 1:40–1:55 | Prepared agreement, broken run | contact-form FAIL card | **Recorded earlier** · broken delivery → FAIL → capture blocked |
| 1:55–2:05 | Manual review panel (prepared) | show "Scripted demo decision" label | scripted demo decision (labelled in app) |
| 2:05–2:25 | Payment tab (bob) | Eligible → Approve capture → Capture | **SIMULATED payment (rehearsal mode)** — banner visible |
| 2:25–2:45 | Landing proof cards; cut to INTEGRATION_PROOF.md table | highlight IDs | **Real PayPal Sandbox · recorded Oct 16, 2026** |
| 2:45–2:50 | Logo + tagline + repo URL | — | github.com/datAgent77/outcomeguard · MIT |

## Screen recording plan

- 1920×1080, browser zoom 125 %, light theme, notifications off, no bookmarks bar.
- Stack: `scripts/demo/start.sh` + `prepare_demo.py --ready`; windows as in GOLDEN_DEMO.md.
- Record each segment separately (clean takes), then edit. Do **not** speed up without the
  on-screen "⏩" mark; do not cut a live wait so that it looks instantaneous.
- Record the real-Sandbox segment from the landing page cards and the GitHub
  `INTEGRATION_PROOF.md` page (text only — no PayPal UI).
- Voice: record narration separately (quiet room, pop filter), sync in editing.
- Export H.264, 1080p, < 2:59; upload to YouTube **Public**; add captions file
  `03b_captions.srt`.

## Backup footage plan

| Risk | Backup |
|---|---|
| Claude slow/down during recording | Re-take later; or use the existing rehearsal screenshots `02-wizard-ai-criteria.png` with caption "recorded earlier" |
| Verification flaky | Use the prepared agreement's fixed run, captioned "recorded earlier" |
| Capture step fails | Re-run `prepare_demo.py --ready` and re-take |
| No time to record | Assemble from `docs/hackathon/screenshots/` (rehearsal) and `docs/p08/sandbox/` (real) with captions — weaker for "Presentation", use only as a fallback |
