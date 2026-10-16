# Resolution, authorization void and refund governance (P08)

> Opening a case never moves money. A refund needs the buyer **and** the provider to approve
> the same proposal, a fresh deterministic policy decision, and a completed PayPal capture.
> AI and service accounts can do none of this. OutcomeGuard does not arbitrate, hold funds
> or replace PayPal's dispute process.

Design: [architecture/P08_RESOLUTION_PLAN.md](architecture/P08_RESOLUTION_PLAN.md).

## The six questions, and where the answer lives

| Question | Answered by |
|---|---|
| What was promised? | locked agreement version + content hash (`resolution_cases.agreement_content_hash`) |
| What evidence exists? | immutable snapshot at opening (run, verdict, results, artifact hashes, manual reviews) + later runs linked as events |
| What went wrong? | case reason, description, selected criteria, both parties' messages |
| What remedy was agreed? | the `accepted` proposal and its two approvals bound to `content_sha256` |
| Who authorized the financial action? | the two approvers + the executor, the `refund`/`void` policy decision, audit log |
| What actually happened at PayPal? | `payment_refunds` / `authorization_voids` with PayPal ids and PayPal-reported status and figures |

## State machines

Case (`domain/resolution_policy.py: TRANSITIONS`, validated on every change; terminal states
are final, also enforced by a DB trigger):

```
open ─▶ under_review ─▶ proposed ─▶ agreed ─▶ executing ─▶ resolved
proposed ─(rejected/withdrawn)─▶ under_review       agreed (non-monetary) ─(close)─▶ resolved
executing ─(unknown / failed)─▶ action_required ─(reconciled)─▶ resolved | ─(retry)─▶ executing
open|under_review|proposed ─▶ cancelled | closed_no_action      under_review|proposed ─▶ rejected
```

Proposal: `open → accepted → executed`, `open → rejected | superseded | withdrawn`. Content
is immutable (trigger); a newer proposal supersedes the open/accepted one.

Refund (`payment_refunds.status`): `requested` (reserved + committed, then sent) →
`completed | pending | failed | unknown`; `unknown → reconciliation_required` when an
automatic replay is no longer safe. Brief mapping: REQUESTED/RESERVED/SUBMITTED =
`requested`; RECONCILIATION_REQUIRED = `reconciliation_required`.

Void (`authorization_voids.status`): `requested → completed | failed | unknown`; while
`requested|unknown` the authorization is `void_in_progress` and cannot be captured.

## Permission matrix

| Action | Buyer admin / approver | Buyer reviewer | Provider admin | Provider approver | Provider (role) | Service account | Other org |
|---|---|---|---|---|---|---|---|
| Read cases | ✓ | ✓ | ✓ | ✓ | ✓ | read | 404 |
| Open case | ✓ | 403 | 403 | 403 | 403 | 403 | 404 |
| Message / link evidence | ✓ | ✓ | ✓ | ✓ | ✓ | 403 | 404 |
| Propose rework / other | ✓ | – | ✓ | ✓ | ✓ | 403 | 404 |
| Propose / approve refund or cancellation | ✓ | – | ✓ | ✓ | 403 | 403 | 404 |
| Execute agreed refund | ✓ | – | ✓ | – | – | 403 | 404 |
| Void authorization | ✓ | – | – | – | – | 403 | 404 |
| Withdraw / close claim | ✓ | – | – (only "remedy carried out") | – | – | 403 | 404 |

The side of an approval is derived from the approver's membership, never from the request;
a DB trigger re-checks that the organization matches the side and that the approval quotes
the proposal's current content hash.

## Financial invariants

1. `Σ refunds in {requested, unknown, pending, completed, reconciliation_required} ≤ captured`,
   checked under the capture row lock (`SELECT … FOR UPDATE`) by the payment layer and by the
   refund policy in the same transaction.
2. Agreement-bound refunds must reference a case, an accepted proposal with buyer **and**
   provider approvals bound to its hash, and an `allowed` refund decision for the same
   capture, amount and currency — DB trigger `og_require_refund_resolution`. The P02
   operator endpoint is refused by the service (`POLICY_REQUIRED`) and by the trigger.
3. One live refund per proposal (partial unique index); `Idempotency-Key` →
   `PayPal-Request-Id og-ref-{key}`; the same key returns the original operation.
4. Integer minor units, no currency conversion (`CURRENCY_MISMATCH`).
5. Fees are not assumed to be returned. PayPal-reported `paypal_fee_returned` and
   `merchant_debit` are stored per refund; null when PayPal reports nothing.
6. An uncertain refund blocks every new refund on the capture (`REFUND_OUTCOME_UNKNOWN`)
   and its amount stays held.
7. Void and capture lock the same authorization row; each refuses while the other is in
   flight or uncertain.

### Refund calculation examples (capture $500.00)

| Refunds so far | Held | Remaining | Proposal | Result |
|---|---|---|---|---|
| — | 0 | 500.00 | partial 100.00 | allowed |
| 100.00 completed, 300.00 completed | 0 | 100.00 | partial 100.01 | `REFUND_EXCEEDS_REMAINING` (rejected already at proposal time) |
| 100.00 + 300.00 completed | 0 | 100.00 | full refund | amount computed by the server = 100.00 |
| 200.00 unknown / reconciliation_required | 200.00 | 300.00 | partial 50.00 | `REFUND_OUTCOME_UNKNOWN` until reconciled |
| 500.00 failed (PayPal 4xx) | 0 | 500.00 | retry same proposal, new key | allowed (failed moved no money) |

## Policy reason codes

Refund: `ACTOR_NOT_AUTHORIZED`, `RESOLUTION_NOT_FOUND`, `RESOLUTION_NOT_AGREED`,
`APPROVAL_MISSING`, `APPROVAL_STALE`, `REFUND_AMOUNT_INVALID`, `REFUND_EXCEEDS_REMAINING`,
`CAPTURE_NOT_COMPLETED`, `CURRENCY_MISMATCH`, `AGREEMENT_MISMATCH`,
`REFUND_ALREADY_COMPLETED`, `REFUND_IN_PROGRESS`, `REFUND_OUTCOME_UNKNOWN`.

Void: `ACTOR_NOT_AUTHORIZED`, `AGREEMENT_MISMATCH`, `CAPTURE_IN_PROGRESS`,
`ALREADY_CAPTURED`, `VOID_IN_PROGRESS`, `VOID_ALREADY_COMPLETED`,
`AUTHORIZATION_NOT_VOIDABLE`, `PROVIDER_CONSENT_REQUIRED` (P06 says the delivery is
verified: cancelling needs an accepted, two-sided `cancel_authorization` proposal).

Every evaluation is stored in `financial_policy_decisions` with reasons, inputs, inputs
hash and figures. Evaluations shown on a screen are audited as `policy.preview.*`
(hidden in the default ledger); evaluations that gate money as `policy.refund.*` /
`policy.void.*`.

## API

| Method | Path | Notes |
|---|---|---|
| POST / GET | `/agreements/{id}/resolutions` | open (buyer finance) / list |
| GET | `/resolutions/{id}` | case, snapshot, proposals + approvals, timeline, financials |
| POST | `/resolutions/{id}/messages` · `/evidence` | message; link a later verification run |
| POST | `/resolutions/{id}/proposals` | `expected_revision`, remedy, amount, rationale, `approve` |
| POST | `/resolutions/{id}/proposals/{pid}/approve` · `/reject` · `/withdraw` | `content_sha256` required |
| POST | `/resolutions/{id}/close` | `resolved`, `closed_no_action`, `cancelled`, `rejected` |
| GET | `/resolutions/{id}/policy?proposal_id=` | fresh refund evaluation (preview) |
| POST | `/resolutions/{id}/refunds` | `{proposal_id}` + **`Idempotency-Key` required** |
| GET | `/resolutions/{id}/refunds` | refunds of the case |
| POST | `/refunds/{id}/reconcile` | read-first reconciliation |
| GET | `/payments/authorizations/{id}/void-policy` · `/void-status` | preview / current operation |
| POST | `/payments/authorizations/{id}/void` | `{reason}`; idempotent per authorization |
| POST | `/voids/{id}/reconcile` | read-first reconciliation |

Errors use the existing shape `{code, message, reasons?}`: 404 for other organizations,
403 for missing roles/human accounts, 409 `POLICY_NOT_SATISFIED` with reason codes, 422 for
invalid amounts/currency, 503 `OUTCOME_UNKNOWN` when PayPal's answer was lost.

## Reconciliation procedures

Refund (`PaymentService.reconcile_refund`):
1. a persisted PayPal refund id → `GET /v2/payments/refunds/{id}`;
2. otherwise `GET` the order and match `payments.refunds[*].invoice_id` = our invoice id
   (`{order reference}-r-{refund id prefix}`); exactly one match → settle, no POST;
3. otherwise `reconciliation_required`: **nothing is re-sent**, the amount remains held and
   new refunds on the capture are blocked. The state is not terminal: reconciling again
   re-reads PayPal (an eventually consistent read may show the refund later). If PayPal
   never shows it, an operator checks the PayPal dashboard (TD-18).

Why no automatic re-send, not even with the same `PayPal-Request-Id` (TD-17, checked
2026-10-16): PayPal's idempotency guide says retention is defined per API, and the v2
"Refund captured payment" reference does not state how long the key is honoured (the
30/45-day figures in PayPal's docs belong to the v1 API). A replay outside an unknown
window could create a second refund, so the system stops instead.

Void (`reconcile_void`): read the authorization; `VOIDED` → completed; still `CREATED` →
replay the same request id; anything else → failed and the capture path reconciles money.
The `PAYMENT.AUTHORIZATION.VOIDED` webhook triggers the same reconciliation;
`PAYMENT.CAPTURE.REFUNDED` reconciles the refund. Webhook replays are deduplicated by event
id (P02).

## Evidence integrity

The snapshot (run id, verdict, per-criterion outcomes, artifact ids + sha256, manual-review
ids incl. a `scripted` flag, payment ids, environment, limitations) is written once and its
hash stored; a trigger rejects any change. Rework is a new submission and run (P04), linked
to the case as an `evidence_linked` event — the original proof is never overwritten.

## AI

No AI feature was added in P08. AI has no role in any resolution or money operation; the
service accounts used by AI integrations are refused for every mutation (tested).

## Tests

| Layer | File | Count |
|---|---|---|
| Pure policy + state machine | `apps/api/tests/test_resolution_policy.py` | 46 |
| Service, MOCKED PayPal with fault injection | `apps/api/tests/test_resolution.py` | 29 |
| HTTP, rehearsal (simulated) payments, webhook | `apps/api/tests/test_resolution_api.py` | 3 |
| Browser, MOCKED API | `apps/web/e2e/mocked/resolution.spec.ts` | 9 |
| Browser, real API, SIMULATED payments | `apps/web/e2e/integration/resolution-flow.spec.ts` | 7 |

Mocked and simulated tests are not PayPal evidence. Real Sandbox evidence:
[INTEGRATION_PROOF.md](INTEGRATION_PROOF.md).

## Limitations

- Internal cases are not PayPal Disputes API cases; chargebacks and buyer disputes opened at
  PayPal are not imported or prevented, and refund availability is not guaranteed.
- No administrator override policy: every refund needs both parties.
- One capture per agreement payment; multi-capture and partial capture remain out of scope.
- A refund whose PayPal outcome cannot be read stays `reconciliation_required` until PayPal
  shows it or an operator resolves it; there is no operator tooling yet (TD-18).
- Notifications (email etc.) do not exist; parties see changes when they open the case.
