# Final submission checklist

Deadline: **Thu Nov 12, 2026, 12:00 pm PT = 23:00 GMT+3** (rules §1). Aim to submit by
**Nov 11**; save a Devpost draft early (drafts are allowed, no edits after the deadline).

## Repository (GitHub)
- [x] Public: https://github.com/datAgent77/outcomeguard
- [x] `LICENSE` = MIT, detected by GitHub (license API returns MIT)
- [ ] **[YOU]** "About" box: add description ("Don't just pay for a promise. Pay for proof.
      Proof-gated PayPal payments with AI-drafted criteria and real-browser verification."),
      topics (paypal, ai, claude, fastapi, nextjs, ag-grid, hackathon) and check that the
      license shows there
- [x] README for judges: problem, real Sandbox IDs, architecture, setup, demo, security boundaries
- [x] No secrets committed (scan before the final push); `.env` git-ignored
- [x] CI green on the final commit (re-check right before submitting)
- [ ] **[YOU]** Freeze: tag the submitted commit (e.g. `v1.0-hackathon`) — approval needed

## Reproducibility ("judges must be able to run it")
- [x] Fresh-clone run verified 2026-10-16 following the README, **without** PayPal or
      Anthropic keys: setup → `scripts/demo/start.sh` → `prepare_demo.py --ready` (39 s:
      broken FAIL, fixed NEEDS_MANUAL_REVIEW) → login → simulated capture; no browser errors.
      (Dependencies were cached on this machine; a first install takes longer.)
- [ ] Repeat the fresh-clone check on the final commit
- [x] Decided 2026-10-24: local-only, no hosted demo

## Video (YouTube)
- [x] Recorded from the running app: `docs/hackathon/video/OutcomeGuard-cinematic.mp4`, **2:16**, 1080p
- [x] Truth labels on screen: LIVE (Claude, Chromium/Lighthouse) · Recorded earlier (broken
      FAIL) · SIMULATED (capture) · Real PayPal Sandbox (IDs, Oct 16)
- [x] No music; no PayPal checkout page or logos (PayPal named in text only — see 06, decision 3)
- [ ] **[YOU]** ElevenLabs narration: confirm your plan's usage/attribution terms
- [ ] **[YOU]** Upload **Public** with title/description/thumbnail/captions from `06_DEVPOST_READY.md`, paste the link into Devpost

## Devpost form (`02_DEVPOST_DRAFT.md`)
- [ ] Name, tagline, description, "built with", tools used, repo link, video link, gallery
- [ ] Sponsor prize: Best Use of AG Grid — opt in if the form has a field (eligible; secondary chance, see 06)
- [ ] Testing instructions (README section) and, if you choose, any test access
- [ ] Eligibility and team/representative details **[YOU]**
- [x] Gallery: `gallery/01…09` — real frames, simulated ones labelled

## PayPal Sandbox precautions
- [x] Real Sandbox evidence already recorded (no new transactions needed for submission)
- [ ] Do not publish Sandbox credentials; judges use their own developer app
- [ ] Never describe a SIMULATED capture as a PayPal payment

## Final pass (day of submission)
1. `git pull`; CI green; secret scan; fresh-clone run.
2. Watch the uploaded video once in an incognito window (public, < 3:00, captions).
3. Submit on Devpost; open the public project page and check every link.
