# OutcomeGuard — project description

**Tagline (≤ 200 characters):** Don't just pay for a promise. Pay for proof. AI turns a job
into measurable checks, real tools verify the delivery, and PayPal captures only when the
proof and the buyer say yes.

## The problem

Every freelance or agency job ends with the same leap of faith. Either the buyer pays first
and hopes the work arrives, or the provider works first and hopes to be paid. When something
is wrong, the argument is "it doesn't work" against "yes it does" — with no shared, objective
record of what was promised or what was delivered. Small businesses and independent
providers carry that risk on every project.

## The solution

OutcomeGuard ties a PayPal payment to **proof that the agreed work was delivered**.

1. **The buyer describes the job in plain English** — "a mobile-friendly landing page for
   $500; the contact form must work; it must be accessible; Lighthouse performance at least 90."
2. **AI turns it into measurable acceptance criteria.** Every criterion quotes the buyer's
   own words; anything phrased as "must" stays blocking; the AI's own ideas are only
   suggestions the buyer can opt into. The buyer locks the agreement — a versioned, hashed
   contract nobody can quietly change.
3. **The payment is authorized on PayPal up front** — the buyer approves in PayPal, funds
   are authorized but not captured.
4. **The provider delivers and real tools verify it**: a real Chromium browser checks the
   layout on a phone-sized screen and submits the contact form to a controlled test
   endpoint; Google Lighthouse measures performance. Every result is stored as evidence.
5. **Humans decide what tools cannot.** Accessibility, for example, goes to an authorized
   reviewer who attests to the exact evidence they saw.
6. **PayPal captures only when the proof and the buyer say yes.** A deterministic policy is
   re-evaluated at the moment of capture; the buyer approves separately; then PayPal
   captures. If something goes wrong later, both parties can agree a partial refund — again
   executed through PayPal and only with both approvals.

## What makes it different

- **Proof-gated payments, not escrow.** OutcomeGuard never holds funds; it uses PayPal's
  authorize-then-capture model and decides *when the merchant's server may capture*.
- **A clear division of labour.** AI drafts criteria and explains evidence. Deterministic
  tools verify. Authorized humans approve. PayPal executes. AI has no financial authority —
  it cannot lock an agreement, decide a review, approve a capture or move money.
- **Evidence you can audit.** Screenshots, Lighthouse reports and traces are stored by
  content hash; reviews and approvals are bound to the exact evidence and policy inputs;
  every event goes into a hash-chained audit log, shown in an AG Grid ledger.
- **Payment safety engineering.** Idempotent PayPal requests, uncertain outcomes reconciled
  by *reading PayPal first* (never by sending a second payment), capture/cancellation
  mutual exclusion, database-level enforcement.

## PayPal integration

PayPal Orders v2 with `intent=AUTHORIZE` and buyer approval; Payments v2 capture, partial
refund and void of authorizations; `PayPal-Request-Id` idempotency on every money movement;
read-first reconciliation of lost responses; webhook signature verification (implemented and unit-tested; live webhook delivery not yet exercised). Proven on
**PayPal Sandbox** on Oct 16, 2026: a proof-governed $500 capture (`70F43933LT884351X`), a
two-party $100 refund (`2DF34389JE394200F`, $400 still refundable) and a cancelled
authorization (`32284282MB448210R`, VOIDED) — all IDs read back from PayPal and documented
with full audit trails in the repository.

## AI integration

Anthropic's Claude (`claude-sonnet-5-5`, Opus selectable) via the official SDK with schema-
constrained outputs:

- **Criteria drafting** — natural-language request → structured, typed acceptance criteria,
  then deterministic checks: quotes must appear in the request, mandatory wording keeps a
  criterion blocking, thresholds the buyer did not write are removed and asked for.
- **Evidence explanation** — plain-language explanations of the deterministic results,
  each claim grounded against stored measurements (ungrounded claims are rejected);
  labelled in the UI as an interpretation, not the verdict.
- Guarded by a USD budget with atomic reservations, prompt-injection containment (the
  delivered page is untrusted input) and an outage path that never blocks the workflow.

## Real-world impact

Freelancers, agencies and the small businesses that hire them get a shared definition of
"done" before work starts, objective evidence when it is delivered, and payment that follows
the evidence — fewer disputes, faster payment for good work, and a clear record when
something goes wrong. The same pattern extends to any digital deliverable with measurable
acceptance criteria.

## Honest scope

PayPal Sandbox only (no real money). Not an escrow service, not an autonomous arbitrator
and not a replacement for PayPal disputes. One service category (landing pages) is
implemented end to end. Known limitations are documented in the repository.
