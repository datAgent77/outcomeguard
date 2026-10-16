# P08 — Resolution, authorization void and refund governance (plan)

Status: plan written before implementation (2026-10-16). Baseline: P07 accepted, `265e7b4`.

## 0. What already exists and is reused

| Capability | Where | Reuse |
|---|---|---|
| Refund with balance check, `PayPal-Request-Id = og-ref-{key}`, idempotent key replay, UNKNOWN on lost response, PayPal-reported fee/net breakdown | `PaymentService.refund_capture / _send_refund / _settle_refund` (P02) | kept; extended with a mandatory governance gate for agreement-bound captures and read-first reconciliation |
| Refundable balance: `capture − Σ refunds in {requested, unknown, pending, completed}` | `refundable_balance`, `REFUND_HOLDS_BALANCE` | kept; `reconciliation_required` added to the holding set |
| Void at PayPal | `gateway.void_authorization` (HTTP + rehearsal) | wrapped by a new governed `void_authorization` service method; no product use before P08 |
| Capture reservation under the authorization row lock, UNKNOWN + read-first reconcile | P06 | void uses the same lock and state markers |
| Pure policy + persisted decisions + inputs hash | `domain/policy.py`, `policy_decisions` | same pattern in `domain/resolution_policy.py` |
| Hash-chained audit, RBAC (`Principal`), org isolation (404) | P01/P03/P06 | reused for every new action |

**Defect found during inspection:** the operator-gated P02 endpoint
`/dev/payments/captures/{id}/refunds` can refund an agreement-bound capture without any
governance (P06 closed the equivalent hole for captures only). P08 makes the governance
gate mandatory in the service (`POLICY_REQUIRED`) and adds a database trigger, with
regression tests.

## 1. Domain entities and state machines

New tables (Alembic migration):

- `resolution_cases` — agreement, locked version + content hash, capture (nullable for
  pre-capture cases), buyer org, merchant org, opener, reason category, description,
  requested remedy (+ amount), **evidence snapshot** (JSONB, immutable), status, outcome,
  closure reason, `revision` (optimistic concurrency), timestamps.
- `resolution_events` — append-only timeline: messages, evidence links, status changes,
  proposal/approval/execution events (author, side, body, data).
- `resolution_proposals` — numbered per case; author, side, remedy, amount/currency,
  rationale, evidence refs, `content_sha256`; content immutable (trigger), only
  `status`/`decided_at` change.
- `resolution_approvals` — append-only per proposal and side (approve/reject), bound to the
  proposal `content_sha256` the approver saw; unique approval per (proposal, side).
- `financial_policy_decisions` — append-only evaluations for `refund` and `void`
  (decision, reasons, inputs, inputs hash, refs).
- `authorization_voids` — one governed void operation per authorization (request id,
  status, policy decision, case/proposal refs, PayPal-reported status, error).
- `payment_refunds` gains `resolution_case_id`, `proposal_id`, `policy_decision_id`.

Case states:

```
open ──(other side writes / proposes)──▶ under_review ──(proposal)──▶ proposed
proposed ──(both sides approve)──▶ agreed ──(refund/void reserved)──▶ executing ──▶ resolved
proposed ──(proposal rejected)──▶ under_review
agreed (non-monetary remedy) ──(close)──▶ resolved
executing ──(UNKNOWN / reconciliation required / PayPal rejection)──▶ action_required
action_required ──(reconciled: completed)──▶ resolved ; (retry allowed after failure)──▶ agreed
open|under_review|proposed ──(buyer withdraws)──▶ cancelled
open|under_review|proposed ──(buyer closes, no remedy)──▶ closed_no_action
under_review|proposed ──(buyer accepts provider's rejection of the claim)──▶ rejected
```

Terminal: `resolved`, `closed_no_action`, `rejected`, `cancelled`. At most one
non-terminal case per capture (partial unique index); a pre-capture case is keyed by
agreement.

Proposal: `open → accepted (both sides approved) → executed` · `open → rejected` ·
`open → superseded` (a newer proposal on the case) · `open → withdrawn` (author).

Remedies: `rework`, `new_verification`, `partial_refund`, `full_refund`,
`cancel_authorization`, `other` (no money movement), `no_remedy`.

Refund operation (existing `payment_refunds.status`): `requested` (reserved and committed;
the PayPal call follows) → `completed | pending | failed | unknown`; `unknown →
reconciliation_required` when a safe automatic replay is no longer possible. Mapping to
the P08 brief: REQUESTED/RESERVED/SUBMITTED = `requested`, PENDING = `pending`,
COMPLETED = `completed`, FAILED = `failed` (PayPal rejected, no money moved), UNKNOWN =
`unknown`, RECONCILIATION_REQUIRED = `reconciliation_required`.

Void operation: `requested → completed | failed | unknown`; authorization marker
`void_in_progress` while requested/unknown (blocks capture), `voided` after.

## 2. Permission matrix

| Action | buyer_admin | buyer_approver | buyer_reviewer | merchant_admin | merchant_approver | provider | service account | other org |
|---|---|---|---|---|---|---|---|---|
| Read cases of own agreements | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | read only | 404 |
| Open case | ✓ | ✓ | – | – | – | – | ✗ | 404 |
| Message / link evidence | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | 404 |
| Propose non-monetary remedy | ✓ | ✓ | – | ✓ | ✓ | ✓ | ✗ | 404 |
| Propose refund / cancel authorization | ✓ | ✓ | – | ✓ | ✓ | – | ✗ | 404 |
| Approve/reject non-monetary proposal (own side) | ✓ | ✓ | – | ✓ | ✓ | ✓ | ✗ | 404 |
| Approve/reject monetary proposal (own side) | ✓ | ✓ | – | ✓ | ✓ | – | ✗ | 404 |
| Execute approved refund | ✓ | ✓ | – | ✓ | – | – | ✗ | 404 |
| Void authorization | ✓ | ✓ | – | – | – | – | ✗ | 404 |
| Withdraw / close case (buyer outcomes) | ✓ | ✓ | – | – | – | – | ✗ | 404 |
| Reconcile refund / void | ✓ | ✓ | – | ✓ | – | – | ✗ | 404 |

All mutations require a human account. Members get 403, non-members 404. A side can only
approve for its own organization (buyer approval cannot be forged by the provider and vice
versa: the side is derived from membership, never from the request).

## 3. Financial invariants

1. `Σ refunds in {requested, unknown, pending, reconciliation_required, completed} ≤ capture amount`,
   enforced under `SELECT … FOR UPDATE` on the capture row (existing) and re-checked by the
   resolution policy in the same transaction.
2. Every agreement-bound refund references a case, an **accepted** proposal with a buyer
   *and* a provider approval bound to its content hash, a `refund` policy decision with
   decision `allowed`, and the same capture — enforced by a DB trigger at insert.
3. One live refund per proposal (partial unique index on `payment_refunds.proposal_id`
   excluding `failed`); amount and currency equal the proposal.
4. Currency is never converted; money is integer minor units (`Money`).
5. Fees are not assumed returned: refund rows store PayPal-reported
   `paypal_fee_returned` and `merchant_debit` separately; null when PayPal did not report.
6. A void is not a refund; the UI never shows an uncaptured authorization as paid.
7. Void and capture exclude each other: both lock the authorization row; void requires
   `authorized` and no capture row in `requested|unknown|pending|completed`; capture
   requires `authorized` (so `void_in_progress`/`voided` block it); `_apply_authorization`
   never overwrites the in-flight markers with PayPal's still-`CREATED` view.

## 4. Concurrency and idempotency

- Refund: client `Idempotency-Key` (required) → `PayPal-Request-Id og-ref-{key}`; same key
  returns the original operation; a different key for the same proposal is refused while
  one is live (`REFUND_IN_PROGRESS` / `REFUND_ALREADY_COMPLETED`).
- Void: deterministic request id `og-void-{authorization id}`; repeated requests return
  the existing operation.
- Proposals: `expected_revision` of the case; approvals quote the proposal
  `content_sha256` → `APPROVAL_STALE` on mismatch or when the proposal is no longer open.
- No transaction is open during a PayPal call: reserve + commit → call → settle (new tx).

## 5. Evidence and agreement bindings

At opening the case stores an immutable snapshot: agreement version id + content hash,
latest completed run id + evidence snapshot hash, selected criterion ids (must belong to
the version), artifact ids + sha256 (must belong to the run), manual-review decision ids
of that run, capture/authorization/order ids. Missing items are recorded as explicit
limitations. Later evidence (rework) is a new submission/run through the existing P04
endpoint, linked to the case as an event with its own snapshot hash; old evidence is never
mutated.

## 6. PayPal Sandbox integration

Reuses `refund_capture` and `void_authorization` (`POST /v2/payments/authorizations/{id}/void`)
with `PayPal-Request-Id`. After money moves, the capture/authorization are re-read from
PayPal (`_refresh_mirrors`). The rehearsal gateway mirrors refunds onto the order
(`payments.refunds`, `invoice_id`) so read-first reconciliation behaves the same in
simulation. Webhooks: `PAYMENT.AUTHORIZATION.VOIDED` also settles a pending void;
`PAYMENT.CAPTURE.REFUNDED` reconciles the refund. Internal resolution cases are not PayPal
Disputes API cases.

## 7. Failure and reconciliation strategy

Refund response lost → `unknown` (balance stays held, case `action_required`).
Reconcile (read first):
1. persisted PayPal refund id → `GET /v2/payments/refunds/{id}`;
2. otherwise `GET` the order and match `payments.refunds[*].invoice_id` = our refund
   invoice id (exactly one match) → settle from `GET refund`;
3. no match and the operation is younger than the replay window (6 h, conservative) →
   replay with the **same** `PayPal-Request-Id`;
4. otherwise → `reconciliation_required` (operator; balance stays held). Never a new key.

Void response lost → `unknown`; reconcile reads the authorization: `VOIDED` → completed;
`CAPTURED` → void failed, capture is reconciled; still `CREATED` → replay the same request
id (voiding is idempotent at PayPal).

## 8. UI flows

Agreement workspace gains a **Resolution** tab: case list + "Open a case" wizard (issue,
criteria, evidence, remedy, review; states that nothing is reversed automatically) and a
case workspace (positions, evidence snapshot vs later runs, timeline, proposals with
approvals per side, financial panel: captured / refunded / held / remaining / proposed,
policy reasons, explicit refund confirmation, UNKNOWN + reconcile). The Payment tab gains
**Cancel authorization** (void) with an explanation that it is not a refund. Ledger rows
for case, proposal, approval, void and refund events.

## 9. Test and acceptance matrix

Pure policy (refund + void reason codes); service tests with the fake gateway (void:
valid/unauthorized/captured/UNKNOWN capture/concurrent with capture/idempotent/timeout;
refund: partial/full/multiple/over/currency/duplicate/concurrent/5xx/lost → UNKNOWN →
read-first/held balance/PayPal-reported figures; dev-endpoint bypass); lifecycle over HTTP
(open, respond, negotiate, missing/stale approval, org isolation, service account, evidence
immutability, rework run, closure audit); migration up/down/up; mocked browser tests and the
real-API browser flow with simulated payments; real Sandbox refund + void demonstration
after the user says READY.
