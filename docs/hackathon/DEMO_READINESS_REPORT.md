# Demo readiness report (P09)

> Update 2026-10-25: the recorded demo video is public at https://youtu.be/NC4gV5sbMEg.

Date: 2026-10-16 · Baseline: P08 accepted (`d4ff99c`) · Scope: hackathon demonstration, not
production hardening.

## Needs confirmation (not documented in the repository)

The repository documents the deliverables (real PayPal Sandbox, real AI, public repo,
README, hosted demo *or* reproducible local environment, tests, a three-minute demo script,
Devpost materials — `docs/BUILD_PROMPT_P00.md`). It does **not** document the **judging
criteria, the video length limit, whether a public URL is mandatory, or the exact
submission form**. The Devpost page returned HTTP 403 to automated fetching. Please
confirm these before P10.

## What is genuinely impressive

1. **Proof before payment, end to end, on real PayPal Sandbox.** Authorization → evidence →
   deterministic policy → separate buyer approval → capture; plus a two-party refund and a
   void — each re-read from PayPal with IDs in `docs/INTEGRATION_PROOF.md`.
2. **Real verification, not a mock.** Real Chromium (mobile layout, contact form submitted to
   a controlled endpoint), Google Lighthouse with adaptive median, SSRF-guarded egress,
   content-hashed evidence.
3. **AI with a leash.** Claude drafts criteria from plain English (stated vs suggested,
   mandatory wording cannot be weakened) and explains evidence with grounded claims; it can
   never lock, review, approve or move money.
4. **Honest provenance everywhere.** Scripted demo decisions, demo accounts, simulated vs
   Sandbox payments and PayPal-reported vs derived figures are labelled in UI and ledger.
5. **Financial safety engineering a judge can probe.** Idempotency, UNKNOWN outcomes with
   read-first reconciliation, capture/void mutual exclusion, DB triggers, 420 backend tests.

## What looked unfinished or confusing (and what P09 changed)

| # | Finding | Impact | P09 |
|---|---|---|---|
| 1 | The top banner said "PayPal Sandbox only" even when the API ran in **rehearsal** mode (simulated payments) | Could present a simulated payment as Sandbox | **Fixed**: banner reads the API mode; rehearsal shows "REHEARSAL MODE · new payments are SIMULATED" |
| 2 | Evidence cards showed raw JSON (`viewport {"width":390…}`) | Engineering-dashboard feel; judges cannot read it | **Fixed**: one plain sentence per check ("On a 390px-wide phone screen the page is 390px wide — no sideways scrolling"); raw data collapsed under "Technical measurements" |
| 3 | AI / tool / human roles had to be inferred | Core message diluted | **Fixed**: legend "Measured · AI interpretation · Human decision" and a "Measured · Real Chromium browser" label per check |
| 4 | Agreement page needed reading three badges and seven tabs to see "where are we" | Story not visible in 20 s | **Fixed**: 5-step proof pipeline (Promise locked → Delivery verified → Human review → Buyer approves → PayPal capture) at the top |
| 5 | Real Sandbox evidence only in docs | Judges may assume it is simulated | **Fixed**: "Proven end-to-end on PayPal Sandbox" section on the landing page with the real IDs and a link to the proof |
| 6 | No one-command demo startup | Setup risk on demo day | **Fixed**: `scripts/demo/start.sh` / `stop.sh`, `apps/api/scripts/prepare_demo.py` |
| 7 | Seven tabs in the workspace | Still dense | Kept (no redesign); the walkthrough uses Delivery, Evidence and Payment only |

## Critical reliability findings

| Finding | Risk | Status |
|---|---|---|
| Rehearsal banner mislabel (above) | Misleading financial state | Fixed |
| Verifier dev allow-list includes the API (needed for the form sink), so a provider could aim the verifier's browser at OutcomeGuard itself | Dangerous verifier access in the demo environment | **Fixed**: deliveries targeting the API / sink host are refused (`DELIVERY_TARGETS_PLATFORM`), regression test |
| Demo start script initially gave the fixtures a wrong sink URL → the fixed page FAILED | Demo-breaking | Found in rehearsal, fixed; the preparation script now asserts NEEDS_MANUAL_REVIEW |
| Rehearsal payments live in API memory | Restarting the API mid-demo makes simulated orders unreconcilable | Documented: do not restart the API between "authorize" and "capture" |
| Port 3000 is used by another local app | Web would not start | Demo stack uses :3010 |
| Credentials | None in the repo or screenshots; demo password only in git-ignored `.env` | Verified by secret scan |
| Duplicate / unauthorized money movement | — | Unchanged P06/P08 guarantees, regression suites green |

Remaining known debt that does not affect the demo: TD-1–TD-4, TD-7, TD-8, TD-14 (P09
security items were scoped out by the hackathon focus), TD-18, TD-19, npm-audit dev-only
findings (TD-15).

## Measured timings (local MacBook, 2026-10-16)

| Operation | Measured | Source |
|---|---|---|
| AI agreement draft (`claude-sonnet-5-5`) | 11.4 s avg, 18.3 s max (11 calls); 12.3 s in rehearsal | `ai_calls` + rehearsal |
| AI evidence explanation | 14.7 s avg (Sonnet), 37.9 s (Opus) | `ai_calls` |
| Verification run, fixed page | 14–17 s | rehearsal + `prepare_demo.py` |
| Verification run, broken page | 24–26 s (contact form waits for the success text before failing) | same |
| Lighthouse (per run) | ~10 s | `verification_results` |
| Queue wait before a run starts | 0.6 s avg | `verification_runs` |
| Payment policy evaluation (P06, persisted) | 27 ms median | API timing |
| Evidence report data (`GET run`) | 7 ms median | API timing |
| Workspace overview / ledger | 98 ms / 16 ms median | API timing |
| Whole golden path, automated (AI → broken → fixed → simulated pay → review → capture → ledger) | ~60 s | Playwright rehearsal |
| Demo stack start / data preparation | ~1 s / ~39 s | scripts |

Too slow to run all of it live in three minutes: **the broken-page run (25 s) and the AI
explanation (15 s)**. Recommendation: run the AI draft and the *fixed* verification live
(≈12 s + ≈15 s, narrated), show the broken-page FAIL from the prepared backup agreement
(clearly labelled as prepared earlier), skip "Explain with AI" or show an existing one.

## Prioritised list before submission

1. Confirm judging criteria / video limit / hosted-URL requirement (above).
2. Rehearse the golden demo twice on the demo machine with `scripts/demo/start.sh` +
   `prepare_demo.py` (see DEMO_CHECKLIST.md).
3. Decide whether the live payment is simulated (recommended) or a real Sandbox approval;
   if real, follow the Sandbox precautions in the checklist.
4. Record the video from the same script (P10).
