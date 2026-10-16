# Golden demo — three minutes

**Story:** Acme Outdoor (a small business) pays Northwind Studio (a freelancer) $500 for a
landing page — but only for proof that it works.

**Positioning line (first 20 s):** *"Freelance payments today are a leap of faith. OutcomeGuard
turns the promise into measurable checks, verifies the delivery with real tools, and lets
PayPal capture the money only when the proof — and the buyer — say yes."*

## Setup (before the audience arrives)

```bash
scripts/demo/start.sh                                            # rehearsal mode, :3010
DEMO_PASSWORD=… uv run --directory apps/api python scripts/prepare_demo.py
```

`prepare_demo.py` prints the URL of the **prepared backup agreement** (broken FAIL + fixed
NEEDS_MANUAL_REVIEW + scripted review already done). Open these tabs:

| Tab | URL | Signed in as |
|---|---|---|
| A | http://localhost:3010/ (landing) | — |
| B | http://localhost:3010/agreements/new | alice@acme.demo (buyer admin) |
| C | backup agreement `?tab=evidence` (printed by the script) | alice@acme.demo |
| D | `docs/INTEGRATION_PROOF.md` on GitHub (real Sandbox IDs) | — |

Different users need separate browser profiles (one session cookie per browser). Use:
profile 1 alice (buyer admin), profile 2 maya (provider), profile 3 bob (buyer approver),
profile 4 rita (reviewer). Password: the operator-chosen `DEMO_PASSWORD`.

## Walkthrough

| Time | Screen | Action | Expected | Live / prepared |
|---|---|---|---|---|
| 0:00–0:20 | A landing | Read the hero; point at "Proven end-to-end on PayPal Sandbox" | IDs of the real capture, refund and void | Prepared (real Sandbox, Oct 16 2026) — say so |
| 0:20–0:45 | B wizard (alice) | "Use the example request" → **Generate Acceptance Criteria** | ~12 s; buyer-stated criteria with quotes, AI suggestion off by default, "must" stays blocking | **Live** (real Claude call) |
| 0:45–1:00 | B config → lock | Standard form fields, uncheck HTTPS (local fixtures), deadline → Create → Propose → **Approve and lock** | Pipeline step 1 ✓ "Promise locked" | **Live** |
| 1:00–1:20 | C evidence (backup) | Show the **broken** run | FAIL: "the success message never appeared"; capture would be blocked | **Prepared earlier** (25 s run) — say "recorded 10 minutes ago" |
| 1:20–1:45 | new agreement, Delivery tab (maya) | Submit `http://127.0.0.1:8020/landing-page-fixed/` | ~15 s: "Verification running" → Needs human review; 4 PASS, accessibility pending | **Live** (real Chromium + Lighthouse) |
| 1:45–2:00 | Evidence | Legend "Measured / AI / Human"; open a screenshot artifact | Plain-English results; Lighthouse 100 ≥ 90 | **Live** |
| 2:00–2:15 | Payment (alice) | Start checkout → rehearsal approval page → Authorize | Banner + badge **SIMULATED**; "authorized, not captured"; policy: *waiting for human review* | **Live, simulated** — say "simulated here; the real Sandbox run is on the landing page" |
| 2:15–2:30 | Evidence (rita) | Record review: Satisfied, rationale "SCRIPTED DEMO DECISION: …", attest, confirm | "Scripted demo decision — not a human review" | **Live, scripted decision** |
| 2:30–2:50 | Payment (bob) | Policy "Eligible" → **Approve capture** → **Capture $500.00** | Payment captured (SIMULATED); policy now ALREADY_CAPTURED | **Live, simulated** |
| 2:50–3:00 | Ledger | Filter the agreement | Trail with provenance; "Verified on Sandbox" capture/refund IDs in D | Live |

Optional 15 s (if time): on the P08 refund agreement show the resolved case — "$100 refunded
after both sides approved, $400 still refundable" — real Sandbox, prepared.

## What to say about "live vs prepared"

- Live in the room: AI draft, verification of the fixed page, policy decisions, simulated
  payment, scripted review.
- Prepared earlier (say it): the broken-page run (backup agreement) and the real PayPal
  Sandbox transactions (landing page + INTEGRATION_PROOF).
- Never say a simulated capture moved Sandbox money; the UI says SIMULATED in two places.

## Backup plan

| Failure | Fallback |
|---|---|
| AI provider slow / down | Wizard shows the honest error; switch to tab C (backup agreement already locked with the canonical criteria) |
| Verification slow (> 40 s) / worker down | Tab C has both runs; continue from its Evidence tab |
| Lighthouse fails | Run shows INCONCLUSIVE honestly; use tab C |
| API down | `scripts/demo/stop.sh && scripts/demo/start.sh` (≈1 s); rehearsal payments are in memory — restart **before** starting a payment, not between authorize and capture |
| Everything down | `docs/hackathon/screenshots/` and `docs/p08/sandbox/` show the same flow, labelled |
