# P06 — Deterministic payment governance: design

> **Status: design for review. Not implemented.** Implementation starts only after P05
> acceptance and explicit approval.

## Invariants (enforced in code and, where possible, in the database)

1. **No verified evidence + no required human approvals = no capture.**
2. A verification `PASS` alone never triggers capture; capture is always a separate,
   explicit, authorized human action.
3. AI output is never an input to any eligibility or money-moving function (import-level
   isolation already test-enforced in P05).
4. Unresolved **blocking** `manual_review_required` criteria block capture unless an
   authorized human-review decision satisfies each of them.
5. Every decision is bound to exact ids and hashes; changing any bound object invalidates
   the decision.

## New records

| Table | Purpose | Key fields |
|---|---|---|
| `manual_review_decisions` | Authorized human decision on one manual-review criterion | agreement_version_id, agreement content_hash, criterion_id, verification_run_id, evidence_snapshot_sha256 (hash of the run's results + artifact hashes the reviewer saw), decision (`satisfied` / `not_satisfied`), reviewer_user_id, reviewer_role, rationale, decided_at — append-only; unique (run, criterion) |
| `policy_decisions` | Output of the deterministic policy for a capture request | payment_authorization_id, agreement_version_id, verification_run_id, decision (`BLOCKED`/`NEEDS_REVIEW`/`ELIGIBLE_FOR_APPROVAL`/`CAPTURE_APPROVED`/`CAPTURED`), reasons (structured codes), inputs_sha256, evaluated_at — append-only |
| `approvals` (exists, subject `capture`) | Buyer capture approval | content_hash = policy inputs hash, approver identity/role |

`payment_captures` already has `verification_run_id`, `policy_decision_id`, `approval_id`;
P06 adds the missing foreign key for `policy_decision_id` and a DB check that a capture row
cannot be inserted without all three.

## Manual-review decision flow

* Role: new `reviewer` role (buyer organization) or `buyer_approver`; the provider and the
  merchant can never decide their own work. AI cannot call this endpoint (no service
  principal for AI exists).
* The reviewer sees the criterion instructions, the run's evidence and artifacts, and
  submits `{criterion_id, run_id, evidence_snapshot_sha256, decision, rationale}`.
* The server recomputes the evidence snapshot hash; a mismatch (stale view) is rejected.
* A decision is valid only for that run and agreement version. A new run or a new locked
  version requires new decisions.

## Policy function (pure)

```
evaluate(agreement_version, authorization, run, results, review_decisions, approvals, now)
  -> PolicyDecision(decision, reasons[])
```

Checks, in order, each producing a reason code when it fails:

| Check | Reason code |
|---|---|
| version is LOCKED and is the agreement's current locked version | `AGREEMENT_NOT_LOCKED`, `AGREEMENT_SUPERSEDED` |
| payment order bound to this version; amount + currency equal the version | `AMOUNT_MISMATCH`, `VERSION_MISMATCH` |
| authorization `authorized`, not expired (`expires_at` > now + safety margin), payee = merchant | `AUTH_NOT_CAPTURABLE`, `AUTH_EXPIRED`, `MERCHANT_MISMATCH` |
| run is `completed`, bound to the same version and content hash, newer than any later evidence submission | `NO_COMPLETED_RUN`, `RUN_VERSION_MISMATCH`, `STALE_EVIDENCE` |
| every blocking criterion: stored outcome `pass`, or `manual_review_required` with a `satisfied` decision bound to this run/version | `BLOCKING_CRITERION_FAILED`, `MANUAL_REVIEW_PENDING`, `MANUAL_REVIEW_REJECTED` |
| no capture in `requested`/`unknown`/`completed` for this authorization | `CAPTURE_IN_PROGRESS`, `ALREADY_CAPTURED` |

Outcome: any failure → `BLOCKED` (or `NEEDS_REVIEW` when only manual reviews are pending);
all pass → `ELIGIBLE_FOR_APPROVAL`. Only a buyer approval bound to that policy decision's
`inputs_sha256` moves it to `CAPTURE_APPROVED`.

## Capture flow

1. `POST /payments/{authorization_id}/capture-requests` (buyer approver, Idempotency-Key):
   lock the authorization row; re-run `evaluate`; persist the `policy_decision`; require
   `ELIGIBLE_FOR_APPROVAL`; persist the capture `approval` bound to the decision hash.
2. In the same request, re-evaluate immediately before reserving the capture (closes the
   window between approval and capture), then call the existing P02
   `capture_authorization` with `verification_run_id`, `policy_decision_id`,
   `approval_id` — reserve → call → settle, deterministic PayPal-Request-Id.
3. `UNKNOWN` outcomes keep the P02 reconcile path; the policy decision stays
   `CAPTURE_APPROVED` until reconciliation records `CAPTURED` or a definite failure.

## Concurrency, staleness, recovery

* Row lock on the authorization + unique active capture (P02) prevent double capture from
  concurrent approvals; two approvers racing produce one capture and one
  `ALREADY_CAPTURED`/`CAPTURE_IN_PROGRESS`.
* New evidence submission, a new locked agreement version, or a new verification run after
  a decision → earlier policy decisions and manual-review decisions no longer match their
  bound hashes → re-evaluation returns `STALE_EVIDENCE` / `AGREEMENT_SUPERSEDED`.
* Authorization near expiry → `AUTH_EXPIRED` before any PayPal call; reauthorization is out
  of scope (KNOWN_LIMITATIONS).
* Every policy evaluation, review decision, approval and capture transition writes an audit
  event (hash chain, P02).

## Developer-endpoint bypass protection

* `/dev/payments/*` (P02 proof driver) is disabled unless `ENABLE_SANDBOX_PROOF=true`, and
  P06 additionally refuses to start the app with it enabled when `APP_ENV=production`.
* The P02 `capture_authorization` service method gets a required `policy_decision_id`
  argument for any order bound to an agreement version; only orders without an agreement
  (the sandbox-proof driver) may use the bare path, and a DB check forbids capture rows on
  agreement-bound orders without `policy_decision_id`/`approval_id`/`verification_run_id`.

## Tests planned

Pass alone does not capture; manual review pending blocks; reviewer decision on a stale
run is rejected; provider cannot review own work; AI principal cannot reach review or
capture endpoints; amount/version/merchant mismatch; expired authorization; concurrent
approvals; capture uncertainty → reconcile; dev endpoint refused in production; DB check
rejects incomplete capture rows.
