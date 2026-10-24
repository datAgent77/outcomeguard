# Golden demo — three minutes

**Story:** Acme Outdoor (a small business) pays Northwind Studio (a freelancer) $500 for a
landing page — but only for proof that it works.

**Centre of the show (live):** real Claude criteria generation and real Chromium +
Lighthouse verification. **Shown as recorded:** the broken-page FAIL. **Shown as
SIMULATED:** the capture on stage. **Shown separately as real:** the PayPal Sandbox capture,
refund and void (landing page + `docs/INTEGRATION_PROOF.md`).

## Timing — what was measured, and what was not

Two **human-paced automated rehearsals** (2026-10-16): a script played the walkthrough
with real narration length and human-like interaction. These are **estimates, not human
rehearsals**; the presenter's own two timed runs go in the table at the end.

| Rehearsal | Model | Total | AI draft (server) | Fixed-page verification (server) |
|---|---|---|---|---|
| 1 | 140 words/min, 0.9 s per click, 2 s per window switch | **2:24** | 15.3 s | 13.8 s |
| 2 (conservative) | 130 words/min, 1.8 s per click, 2 s per window switch | **2:40** | 15.0 s | 12.2 s |

Per-segment results: `docs/hackathon/rehearsals/rehearsal-{1,2}.json`. Not modelled: stumbles,
questions, slow projector or network. **Margin: 20–36 s.** If a human run exceeds 3:00, cut
in this order: shorten the hook (−8 s), skip segment 6 "broken FAIL" (−14 s), skip the
evidence scroll (−5 s).

## Before the audience (untimed)

```bash
scripts/demo/start.sh                                                     # rehearsal mode, web :3010
DEMO_PASSWORD=… uv run --directory apps/api python scripts/prepare_demo.py --ready
```

`--ready` (rehearsal only; refused in Sandbox mode) prepares one agreement with the broken run
(FAIL), the fixed run (NEEDS_MANUAL_REVIEW), the **scripted** accessibility review and a
**SIMULATED** authorization — so the only cross-account action left on stage is the buyer
approver's capture. It prints the agreement id. **Do not restart the API afterwards**
(rehearsal payments live in API memory).

Open four browser windows (separate profiles = separate sessions), sign in, and park them:

| Window | Profile | Page |
|---|---|---|
| L | (none) | http://localhost:3010/ landing |
| A | alice@acme.demo | `/agreements/new` |
| A2 | alice@acme.demo (second tab) | prepared agreement `?tab=evidence&run=<broken run>` (select the broken run in the run selector) |
| M | maya@northwind.demo | `/provider` |
| B | bob@acme.demo | prepared agreement `?tab=payment` — must show "Eligible for buyer approval" |

Account switching on stage = switching windows (≈2 s), never logging in. rita's review is
pre-recorded and labelled SCRIPTED.

## Walkthrough (≈2:24–2:40)

| # | ~Start | Window | Narration (say) | Do | Expect | Live? |
|---|---|---|---|---|---|---|
| 1 | 0:00 | L | "Hiring a freelancer today means paying for a promise. Either the buyer pays first and hopes, or the freelancer works first and hopes. OutcomeGuard replaces hope with proof: the money is authorized on PayPal up front, but it is captured only when measurable evidence, and the buyer, say yes." | scroll slightly | hero | — |
| 2 | 0:21 | A | "Acme needs a five hundred dollar landing page. The buyer writes the request in plain English. Claude turns it into measurable acceptance criteria, and every criterion quotes the buyer's own words. Mandatory wording stays blocking, and the AI's own idea is only a suggestion, off by default." | **Use the example request** → **Generate Acceptance Criteria** | ~15 s; buyer-stated criteria with quotes; suggestion unchecked | **LIVE (real Claude)** |
| 3 | 0:45 | A | "The buyer adds what only they know, the form fields and the deadline, and locks the agreement. Locking freezes a content hash: nobody can quietly move the goalposts." | Continue → uncheck *Require HTTPS* (local test site) → **Use the standard field set** → Title "Spring campaign landing page" → deadline → Create → Propose → **Approve and lock** → confirm | pipeline step 1 ✓ | LIVE |
| 4 | 1:00 | M | "Now the freelancer delivers. OutcomeGuard opens the page in a real Chromium browser on a phone-sized screen, submits the contact form to a controlled test endpoint, and runs Google Lighthouse. This is happening live." | open the new agreement → Delivery → paste `http://127.0.0.1:8020/landing-page-fixed/` → **Submit** | "Verification running" → ~13–17 s | **LIVE (real Chromium + Lighthouse)** |
| 5 | 1:25 | M | "Three checks passed, in plain language, with the stored screenshots and the Lighthouse report. Accessibility cannot be measured by a tool, so it waits for an authorized human. AI can explain this evidence, but it never decides." | **Open report**, scroll | Needs human review; 3 PASS + 1 pending human review; "Measured / AI / Human" legend | LIVE |
| 6 | 1:42 | A2 | "Here is the same check on the first, broken delivery, recorded before this session: the form was submitted, but the success message never appeared. Capture was blocked." | scroll to the contact-form card | FAIL card | **RECORDED earlier — say so** |
| 7 | 1:57 | B | "On this prepared agreement the review is done and the payment is authorized, simulated here, as the banner says. The policy is re-checked live: eligible. The buyer approves, then captures: two separate, explicit actions." | **Approve capture…** → confirm → **Capture $500.00 via PayPal…** → confirm | "Payment captured (SIMULATED)" + orange REHEARSAL banner | **LIVE, SIMULATED payment** |
| 8 | 2:15 | L | "And this is not only a simulation. The same flow ran on PayPal Sandbox: a governed capture, a refund both parties approved, and a cancelled authorization, each read back from PayPal. Don't just pay for a promise. Pay for proof." | scroll to "Proven end-to-end on PayPal Sandbox" | real IDs | **REAL Sandbox, recorded Oct 16** |

## Rules for honest presentation

- Say "recorded earlier" for segment 6 and "recorded on PayPal Sandbox on October 16" for
  segment 8. Never call the segment-7 capture a PayPal transaction — the UI shows SIMULATED
  in the banner and on the payment card.
- The accessibility review on the prepared agreement is a **scripted demo decision**; if
  asked, say so (the UI labels it).

## Backup plan

| Failure | Fallback |
|---|---|
| Claude slow (> 30 s) or down | Say "the AI is slow today", switch to window A2 — the prepared agreement has the same canonical criteria |
| Verification slow (> 40 s) / worker down | Continue on A2: switch the run selector to the fixed run (recorded) and say so |
| Lighthouse error | The run shows INCONCLUSIVE honestly; continue on A2 |
| Capture fails / API restarted | Do not retry on stage; jump to segment 8 (real Sandbox proof) |
| Everything down | `docs/hackathon/screenshots/` (rehearsal) and `docs/p08/sandbox/` (real Sandbox), labelled |

## Presenter's timed runs (fill in)

| Run | Date | Total | Over 3:00? | Notes / cuts applied |
|---|---|---|---|---|
| Human 1 | | | | |
| Human 2 | | | | |
