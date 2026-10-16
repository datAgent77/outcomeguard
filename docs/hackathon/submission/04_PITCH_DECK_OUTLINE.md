# Five-slide pitch deck — outline

Visual style: the app's own palette (navy text, white, one blue accent, green/amber/red only
for PASS / REVIEW / FAIL). One idea per slide, large type, real screenshots only.

## 1 · Problem — "Paying for a promise"
- Headline: **Every freelance job ends with a leap of faith.**
- Two columns: *Buyer pays first → hopes the work arrives* · *Provider works first → hopes to be paid*.
- One line: disputes start from "it doesn't work" vs "yes it does" — no shared record of
  what was promised or delivered.
- Visual: none or a simple two-arrow diagram. Speaker note: who it hurts — small businesses
  and independent providers.

## 2 · Solution — "Pay for proof"
- Headline: **Don't just pay for a promise. Pay for proof.**
- One sentence: PayPal authorizes the payment up front; it is captured only when measurable
  evidence — and the buyer — say yes.
- The division of labour, as four chips: **AI drafts · Tools verify · Humans approve · PayPal executes**.
- Visual: `p09-landing-1440.png` (hero crop).
- Note: not escrow — OutcomeGuard never holds funds; uses PayPal authorize → capture.

## 3 · How it works — one agreement, five steps
- The app's proof pipeline: Promise locked → Delivery verified → Human review → Buyer approves → PayPal capture.
- Under each step, the actor: Buyer · Chromium + Lighthouse · Authorized reviewer · Buyer approver · PayPal.
- Visual: `08-evidence-needs-review.png` (pipeline + evidence cards).
- Note: Claude writes the criteria from plain English; every criterion quotes the buyer;
  "must" stays blocking; AI ideas are opt-in.

## 4 · Live proof & technical differentiation
- Left: **Real PayPal Sandbox (Oct 16, 2026)** — capture `70F43933LT884351X` ($500) ·
  refund `2DF34389JE394200F` ($100, both parties approved) · void `32284282MB448210R`.
- Right, five bullets:
  - Real browser + Lighthouse evidence, stored by content hash
  - Deterministic policy re-checked under a lock at capture time
  - Idempotent PayPal requests; lost responses reconciled by reading PayPal — never a second payment
  - AI grounded and budget-capped, with zero financial authority
  - Hash-chained audit trail in an AG Grid ledger; 420 + 47 automated tests
- Visual: `13-ledger-grid.png` thumbnail. Label any simulated screenshot "simulated".

## 5 · Vision & impact
- Headline: **A shared definition of "done" — before the work starts.**
- Impact: fewer disputes, faster payment for good work, a clear record when something goes wrong.
- Next: more deliverable types (APIs, mobile builds, documents), richer accessibility
  tooling, a hosted pilot with freelancers and agencies.
- Close: tagline + github.com/datAgent77/outcomeguard (MIT).
- Avoid: market-size numbers we cannot source; claims of production readiness.
