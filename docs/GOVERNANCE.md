# Payment governance (P06)

> No capture unless every applicable condition holds — re-checked at the moment of capture.
> A verification PASS never captures by itself. AI never authorizes money.

Design: [architecture/P06_POLICY_PLAN.md](architecture/P06_POLICY_PLAN.md).

## Three separate human acts

| Act | Who | Bound to | Record |
|---|---|---|---|
| Agreement approval (P03) | `buyer_approver` / `buyer_admin` | version content hash | `approvals` |
| Manual-review decision | `buyer_reviewer` / `buyer_approver`, **human**, not a member of the merchant organization | run + criterion + agreement version + content hash + **evidence snapshot hash** | `manual_review_decisions` (append-only; DB trigger checks the criterion is a pending manual result of that completed run and version) |
| Capture approval | `buyer_approver` / `buyer_admin`, **human**, not a member of the merchant organization | authorization + **policy inputs hash** the approver saw | `capture_approvals` (append-only, consumed once) |

Service accounts (`users.kind = service`, e.g. an AI integration) are refused for reviews
and capture approvals; no AI code path can create these records.

## Deterministic policy (`domain/policy.py`)

Pure function of `PolicyInputs` (agreement/version, order, merchant, authorization,
capture state, latest submission, latest completed run, criteria, results, reviews).
`inputs_sha256` hashes everything except the clock; expiry is checked against `now` on
every evaluation. Reason codes:

`PAYMENT_NOT_BOUND_TO_AGREEMENT`, `AGREEMENT_NOT_LOCKED`, `AGREEMENT_SUPERSEDED`,
`VERSION_MISMATCH`, `AMOUNT_MISMATCH`, `MERCHANT_MISMATCH`, `AUTH_NOT_CAPTURABLE`,
`AUTH_EXPIRED` (incl. 1 h safety margin), `AUTH_EXPIRY_UNKNOWN`, `ALREADY_CAPTURED`,
`CAPTURE_IN_PROGRESS`, `NO_VERIFICATION_RUN`, `STALE_EVIDENCE`, `NO_COMPLETED_RUN`,
`RUN_VERSION_MISMATCH`, `CRITERION_NOT_EVALUATED`, `BLOCKING_CRITERION_FAILED`,
`VERIFICATION_INCONCLUSIVE`, `MANUAL_REVIEW_PENDING`, `MANUAL_REVIEW_REJECTED`,
`NO_BLOCKING_CRITERIA`.

Decision: only `MANUAL_REVIEW_PENDING` → `needs_review`; any other reason → `blocked`;
none → `eligible_for_approval`. A review counts only if it is human, not from the
provider, and bound to the current run, version, content hash and evidence snapshot.

Every evaluation is stored in `policy_decisions` (purpose `preview` / `approval` /
`capture` / `settlement`; append-only).

## Capture (time-of-check = time-of-use)

1. `GET authorization` from PayPal (no DB transaction open).
2. One DB transaction: lock the authorization row → re-gather **all** facts → re-evaluate
   → require `eligible_for_approval` **and** `inputs_sha256 == approval.inputs_sha256` →
   check the approval is unused and the approver still holds a capture role → write a
   `capture_approved` policy decision → reserve the capture row carrying
   `policy_decision_id`, `approval_id`, `verification_run_id` → mark the approval consumed
   → commit.
3. PayPal capture call (no transaction open), deterministic `PayPal-Request-Id`.
4. Settle; `completed` → `captured` policy decision. Lost/uncertain response → `unknown`,
   which blocks every new attempt (`CAPTURE_IN_PROGRESS`) until `reconcile`. Reconcile reads
   first: it looks for the capture on PayPal's order (matched by invoice id / authorization
   link) and settles from it; only if PayPal shows none does it replay the **same**
   `PayPal-Request-Id`. It never issues an independent capture.

Database layer: trigger `og_require_capture_policy` rejects any capture row of an
agreement-bound payment without a `capture_approved` policy decision and a capture
approval for the same authorization and the same inputs hash. The service layer refuses
agreement-bound captures without the governance gate (`POLICY_REQUIRED`), so the P02
`/dev/payments/*` driver cannot bypass policy; the app refuses to start with
`ENABLE_SANDBOX_PROOF=true` when `APP_ENV=production`.

## Endpoints

| Method | Path | Who |
|---|---|---|
| POST | `/auth/login`, `/auth/logout`; GET `/me` | anyone / session |
| POST | `/agreements/{id}/payments` | buyer_admin (human) — order for the locked version |
| GET | `/payments/orders/{id}` | buyer/merchant members — status read from PayPal |
| POST | `/payments/orders/{id}/authorize` | buyer_admin / buyer_approver |
| GET | `/payments/authorizations/{id}/policy` | buyer/merchant members — fresh evaluation |
| POST / GET | `/verification/runs/{id}/manual-reviews` | buyer_reviewer / buyer_approver (human, not provider) / readers |
| POST | `/payments/authorizations/{id}/capture-approvals` `{inputs_sha256}` | buyer_approver / buyer_admin (human, not provider) |
| POST | `/payments/authorizations/{id}/captures` `{approval_id}` | buyer_approver / buyer_admin |
| POST | `/payments/captures/{id}/reconcile` | buyer_approver / buyer_admin |

Non-members receive 404 for everything; members without the role receive 403.

## Authentication

Password login (scrypt) with server-side sessions: 8 h bearer tokens stored only as
SHA-256, logout/revocation, all sessions revoked on password change, 5-failure lockout
for 15 min, generic error messages. Roles are read from the database on every request.
The interim operator-issued tokens (P03) are removed. Demo users get a password only when
an operator supplies one to the operator-gated `/dev/seed`.
