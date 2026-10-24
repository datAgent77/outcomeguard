# Devpost submission draft

Fields marked **[YOU]** need your input or a decision; fields marked **[CONFIRM]** depend on
the submission form, which is visible only to a registered entrant. Nothing has been
submitted.

| Field | Draft |
|---|---|
| Project name | OutcomeGuard |
| Elevator pitch / tagline | Don't just pay for a promise. Pay for proof. AI turns a job into measurable checks, real tools verify the delivery, and PayPal captures only when the proof and the buyer say yes. (178 characters) |
| Built with (tags) | paypal · paypal-orders-api · paypal-payments-api · anthropic · claude · python · fastapi · postgresql · sqlalchemy · next.js · typescript · tailwindcss · ag-grid · playwright · lighthouse |
| Try it out | GitHub: https://github.com/datAgent77/outcomeguard · hosted demo: **none** — local setup per README (decided 2026-10-24) |
| Video demo link (YouTube, public, < 3:00) | **[YOU: upload and paste]** — video is ready: `docs/hackathon/video/OutcomeGuard-cinematic.mp4` (2:16); upload steps in `06_DEVPOST_READY.md` |
| Image gallery | `submission/gallery/01…09-*.jpg` (3:2, real frames with truth labels); order and captions in `06_DEVPOST_READY.md` |
| Sponsor prize opt-in | Best Use of AG Grid **[CONFIRM the form field]** |
| Team / eligibility | **[YOU]** |

## About the project (Devpost markdown)

> Paste-ready. Headings follow Devpost's usual structure; adjust if the form differs **[CONFIRM]**.

### Inspiration
Every freelance job ends with a leap of faith: the buyer pays first and hopes, or the
provider works first and hopes. When something breaks, it's "it doesn't work" against "yes
it does", with no shared record of what was promised. We wanted payments that follow
**proof** instead of trust.

### What it does
OutcomeGuard ties a PayPal payment to proof that the agreed work was delivered.
1. The buyer describes the job in plain English. **Claude** turns it into measurable
   acceptance criteria — each quoting the buyer's words; "must" stays blocking; AI ideas are
   opt-in suggestions. The buyer locks a versioned, hashed agreement.
2. The buyer approves the payment in **PayPal**; it is **authorized, not captured**.
3. The provider submits the delivery. **Real tools** verify it: a Chromium browser checks
   the phone layout and submits the contact form to a controlled endpoint; Google
   Lighthouse measures performance. Evidence is stored by content hash.
4. What no tool can decide (e.g. accessibility) goes to an **authorized human reviewer**.
5. A **deterministic policy** is re-checked at the moment of capture; the buyer approves
   separately; then PayPal **captures**. Later problems can be resolved with a partial
   refund that **both parties** approve.

AI drafts and explains; tools verify; humans approve; PayPal executes. AI can never move money.

### How we built it
- **PayPal:** Orders v2 (`intent=AUTHORIZE`, buyer approval), Payments v2 (capture, partial
  refund, void), `PayPal-Request-Id` idempotency, read-first reconciliation of lost
  responses, webhook signature verification (live webhook delivery not yet exercised).
- **AI:** Anthropic Claude (`claude-sonnet-5-5`) via the official SDK with schema-constrained
  outputs; deterministic post-checks on every draft; evidence explanations grounded against
  stored measurements; USD budget with atomic reservations; prompt-injection containment.
- **Verification:** Playwright Chromium + pinned Lighthouse in a worker behind an
  SSRF-guarded egress proxy; controlled form-sink with HMAC run tokens.
- **Backend:** FastAPI, PostgreSQL, SQLAlchemy, Alembic; pure policy functions; database
  triggers enforcing payment bindings; hash-chained audit log.
- **Frontend:** Next.js 16, TypeScript, Tailwind, **AG Grid Community** for the payment &
  evidence ledger; a backend-for-frontend so the browser never holds an API token.
- 420 backend tests, 47 browser tests, CI on every push.

### Real PayPal Sandbox evidence
- Proof-governed $500 capture: authorization `7HU39463K8073954H` → capture `70F43933LT884351X`
- Two-party $100 refund: capture `95Y56941P3956690M` → refund `2DF34389JE394200F`, $400 remaining
- Cancelled before capture: authorization `32284282MB448210R` → VOIDED
All read back from PayPal; audit trails in `docs/INTEGRATION_PROOF.md`. The video's live
capture runs in **rehearsal mode** and is labelled **SIMULATED** on screen.

### Challenges we ran into
- Making money movement safe under uncertainty: a lost PayPal response must never become a
  second payment — we reconcile by reading PayPal first, and stop for an operator when
  PayPal's records are unclear.
- Keeping AI useful but powerless: stated vs suggested criteria, mandatory wording that
  cannot be weakened, grounded explanations.
- Honest labelling: real Sandbox vs simulated payments, human vs scripted demo decisions.

### Accomplishments that we're proud of
End-to-end proof-gated payments on real PayPal Sandbox, with verification by real tools and
an audit trail a judge can inspect — and a UI that keeps AI, tools, humans and PayPal
visibly separate.

### What we learned
Authorize-then-capture is a powerful primitive for outcome-based payments; the hard part
is not calling the API but deciding — deterministically and auditably — *when* to call it.

### What's next
More deliverable types (APIs, mobile builds, documents), richer accessibility tooling,
notifications, a hosted pilot with freelancers and agencies.

## Tools used (required by the rules)
- **Anthropic Claude** (`claude-sonnet-5-5`; `claude-opus-5` selectable): drafts acceptance
  criteria from the buyer's request; explains verification evidence in plain language.
  It has no authority over agreements, reviews or payments.
- **PayPal Developer Platform (Sandbox):** Orders v2, Payments v2, webhooks.
- **AG Grid Community** (sponsor tool): payment & evidence ledger.
- Playwright/Chromium and Google Lighthouse for deterministic verification.
- **[YOU]** Optional disclosure: the code was written with the help of an AI coding
  assistant (Claude Code) under your direction — decide whether to mention it.
- Video only (not part of the product): narration generated with ElevenLabs text-to-speech.

## How the project was built during the submission period
Repository created on Oct 15, 2026 (within the Oct 1 – Nov 12 period); all functionality was
built after that date — see the git history and `docs/IMPLEMENTATION_PROGRESS.md`.

## Testing instructions for judges
See README → "Run it locally". Rehearsal mode needs no PayPal or AI keys (payments
SIMULATED). Real Sandbox: the judge's own free PayPal developer app. AI drafting: an
Anthropic API key. Demo accounts are created locally with a password the judge chooses
(`DEMO_PASSWORD`). There is no hosted demo and no shared credentials: everything runs
locally, and no secrets are committed.
