# Ready to submit — YouTube + Devpost, step by step

Status on **2026-10-24**: video, captions, thumbnail and gallery are done; nothing has been
uploaded or submitted. Rules re-read in a browser on 2026-10-24 (see "Rules re-check" below).
Deadline: **Thu Nov 12, 2026, 12:00 pm PT = 23:00 GMT+3**. Aim for Nov 11.

## What is ready

| Asset | File | Notes |
|---|---|---|
| Video (2:16, 1080p, H.264/AAC) | `docs/hackathon/video/OutcomeGuard-cinematic.mp4` | Not in git (size). Rebuild: `apps/web/e2e/video/build-cinematic.mjs` |
| Captions | `docs/hackathon/submission/03b_captions.srt` | Timed to the cinematic cut (`make-captions.mjs`) |
| YouTube thumbnail (1280×720) | `docs/hackathon/submission/gallery/youtube-thumbnail.jpg` | |
| Devpost gallery (1500×1000, 3:2) | `docs/hackathon/submission/gallery/01…09-*.jpg` | Real frames; each card keeps its truth label |
| Devpost text | `02_DEVPOST_DRAFT.md` | Paste-ready |

## Step 1 — GitHub "About" box (rules: license must be visible there) [YOU]
Repository → ⚙ next to "About":
- Description: `Don't just pay for a promise. Pay for proof. Proof-gated PayPal payments with AI-drafted criteria and real-browser verification.`
- Website: the YouTube link (after step 2), or leave empty
- Topics: `paypal` `ai` `claude` `anthropic` `fastapi` `nextjs` `ag-grid` `playwright` `lighthouse` `hackathon`
- Check that "MIT license" shows in the About box.

## Step 2 — YouTube upload [YOU]
1. Upload `OutcomeGuard-cinematic.mp4`. Visibility **Public** (rules require public). Audience: "No, it's not made for kids".
2. **Title** (≤100 chars):
   `OutcomeGuard — Don't just pay for a promise. Pay for proof. | PayPal AI Hackathon 2026`
3. **Description** (paste):

```
OutcomeGuard ties a PayPal payment to proof that the agreed work was delivered. Claude turns a plain-English job request into measurable acceptance criteria, a real Chromium browser and Google Lighthouse verify the delivery, humans approve what no tool can measure — and the payment, authorized on PayPal up front, is captured only when the proof and the buyer say yes.

AI drafts · Tools verify · Humans approve · PayPal executes.

What you see in this video
• LIVE: Claude drafting the criteria; Chromium + Lighthouse verifying the delivery (long waits are sped up and labelled on screen).
• RECORDED EARLIER: the broken first delivery that failed and blocked the payment.
• SIMULATED: the capture in this recording runs in rehearsal mode — no PayPal call is made, and the app says so.
• REAL PAYPAL SANDBOX (recorded Oct 16, 2026): a governed $500 capture, a $100 refund approved by both parties, and an authorization voided before capture — every ID read back from PayPal.

0:00 Intro — paying for a promise
0:12 How OutcomeGuard pays
0:26 AI drafts the agreement (live Claude)
0:55 Tools verify the delivery (live Chromium + Lighthouse)
1:20 Broken delivery → FAIL (recorded earlier)
1:30 Humans approve · capture (simulated)
1:51 Real PayPal Sandbox proof

Code (MIT): https://github.com/datAgent77/outcomeguard
Sandbox integration proof: https://github.com/datAgent77/outcomeguard/blob/main/docs/INTEGRATION_PROOF.md

Built for the PayPal AI Hackathon 2026. PayPal Sandbox only — no real money. Narration: AI-generated voice (ElevenLabs). No music.
```

   (YouTube chapters need ≥10 s each, so the 6-second outro is part of the last chapter.)
4. **Thumbnail**: `gallery/youtube-thumbnail.jpg` (custom thumbnails need a verified YouTube account).
5. **Subtitles** → Upload file → "With timing" → `03b_captions.srt` (English).
6. Tags: `OutcomeGuard, PayPal, PayPal Sandbox, Claude, Anthropic, AI, hackathon, freelance payments, Playwright, Lighthouse, AG Grid`. Category: Science & Technology.
7. Open the link in a private window: plays, public, 2:16, captions on.

## Step 3 — Devpost form [YOU]
"Enter a Submission" on paypalaihackathon.devpost.com. Save a **draft** first.
- Name, tagline, "About the project", "Built with", tools, testing notes: copy from `02_DEVPOST_DRAFT.md`.
- **Try it out** links: `https://github.com/datAgent77/outcomeguard` and
  `https://github.com/datAgent77/outcomeguard/blob/main/docs/INTEGRATION_PROOF.md`.
- **Video demo link**: the YouTube URL from step 2.
- **Image gallery** (in this order; the first image is the cover). If there is a caption field, use these:

| # | File | Caption |
|---|---|---|
| 1 | `01-pay-for-proof.jpg` | Don't just pay for a promise. Pay for proof. |
| 2 | `02-who-does-what.jpg` | AI drafts · Tools verify · Humans approve · PayPal executes |
| 3 | `03-claude-criteria.jpg` | LIVE: Claude turns a plain-English request into measurable, buyer-quoted criteria |
| 4 | `04-lock-hash.jpg` | The buyer locks the agreement; a content hash freezes the goalposts |
| 5 | `05-evidence-report.jpg` | LIVE: real Chromium + Lighthouse evidence — 3 passed, 1 waits for a human |
| 6 | `06-broken-fail.jpg` | Recorded earlier: a broken delivery fails and the payment stays blocked |
| 7 | `07-capture-simulated.jpg` | Rehearsal mode: policy re-checked, buyer approves, capture (simulated) |
| 8 | `08-sandbox-proof.jpg` | Real PayPal Sandbox (Oct 16, 2026): capture, two-party refund, void |
| 9 | `09-ag-grid-ledger.jpg` | Payment & evidence ledger built with AG Grid Community (rehearsal data) |

- **Sponsor prize**: opt in to **Best Use of AG Grid** if the form asks (eligible; a secondary
  chance — that prize favours AG Studio dashboards, we use AG Grid Community).
- Eligibility / team / representative fields: yours.
- Submit, then open the public project page and click every link.

## Decisions only you can make
1. **AI-assisted development**: the rules allow technical assistance when the work and ideas are
   yours; disclosing that the code was written with Claude Code under your direction is optional
   but transparent. A line is ready in `02_DEVPOST_DRAFT.md` → "Tools used".
2. **ElevenLabs voice**: check that your ElevenLabs plan allows this use; some plans require
   an attribution. The YouTube description already names it.
3. **PayPal name in the video**: the name appears as text (no logos, no PayPal checkout pages).
   The rules bar third-party trademarks without permission. PayPal is the sponsor, and the
   name is only used to describe the integration. If you want to be fully safe, ask the
   organisers.
4. ~~Hosted demo~~ — **Decided 2026-10-24: no hosted demo** (no Render deployment). Judges run it locally per the README
   (meets the rule), and the video shows it working end to end.
5. **Freeze tag**: `v1.0-hackathon` on the submitted commit.

## Resources page check (2026-10-25, browser)
<https://paypalaihackathon.devpost.com/resources> and its sub-pages (AG Grid, FAQs, Updates):
- **Inspiration** list includes "pays contractors automatically when a job is marked complete"
  and payouts "the moment a task is approved": a direct fit; noted in the Devpost Inspiration text.
- **Best Use of AG Grid** judges "what your dashboard looks like and how you build it" and
  points to AG Studio (custom widgets, theming, Studio Agent Framework). We are eligible
  (AG Grid Community ledger) but not built for that brief: treat it as a secondary chance.
  Decided: no AG Studio work (no new features).
- PayPal **AI Toolkit / Agent Toolkit / MCP server** are suggested, not required. "Best Use
  of Agentic Commerce" rewards agents that transact; OutcomeGuard deliberately keeps AI away
  from money movement, so our natural categories are Best Use of PayPal + AI, Best Demo
  Delivery and Most Impactful.
- FAQs: sponsor prizes are considered automatically per tool used; at most one sponsor prize +
  one grand or honorable mention prize. Updates: webinar recording and FAQ only; no rule changes.
- Questions to organisers: PayPal Discord (linked on the resources page).

## Rules re-check (2026-10-24, browser)
Same as `00_REQUIREMENTS_VERIFIED.md`. Points that matter:
- Deadline, video (< 3 min, public YouTube, no unlicensed trademarks or music) and repository
  (public, license visible in About) rules are unchanged.
- Honorable mentions also list **Best Use of Agentic Commerce**. Prize limit: one grand prize
  or one honorable mention, plus one sponsor prize.
- Judging "may utilize … automated AI-driven analysis". This is one more reason to keep the
  README, the description and the truth labels precise.
- "Judges are not required to test the Project and may choose to judge based solely on the
  text description, images, and video". The gallery and the video carry the case on their own.
