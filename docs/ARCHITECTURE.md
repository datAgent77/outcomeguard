# Architecture

> Status: P01/P02. Sections marked *(planned)* describe components not built yet.

## Principle

```
AI interprets the promise → Tools verify the evidence → Policy determines eligibility
→ Humans authorize the money → PayPal executes the transaction
```

Financial authority flows only through deterministic code paths. The LLM produces
*observations* (proposed criteria, summaries, explanations) that are stored and shown,
but no AI output is an input to a function that moves money.

## Components

```
            ┌──────────────┐  server-side fetch   ┌───────────────────────────┐
 browser ──▶│ apps/web     │─────────────────────▶│ apps/api (FastAPI)        │
            │ Next.js 16   │                      │  api/        HTTP routes  │
            └──────────────┘                      │  services/   payments,    │
                 │ buyer approval                 │              webhooks     │
                 ▼                                │  paypal/     gateway      │──▶ PayPal Sandbox
            PayPal Sandbox ──return/webhook──────▶│  models/     SQLAlchemy   │    (Orders v2,
                                                  └─────────────┬─────────────┘     Payments v2)
                                                                │
                                                  ┌─────────────▼─────────────┐
                                                  │ PostgreSQL (Alembic)      │
                                                  └─────────────▲─────────────┘
                                                                │ jobs (planned P04)
                                                  ┌─────────────┴─────────────┐
                                                  │ workers/verifier (planned)│──▶ delivery URL
                                                  │ Playwright + Lighthouse   │    (SSRF-guarded)
                                                  └───────────────────────────┘
```

| Path | Role |
|---|---|
| `apps/api/src/outcomeguard/config.py` | Env config; Sandbox-only guard; `public_summary()` is the only config view that leaves the process |
| `…/money.py` | Integer minor units; Decimal conversion at the PayPal boundary only |
| `…/paypal/gateway.py` | `PayPalGateway` protocol + `HttpPayPalGateway` (HTTPX). 4xx → `PayPalAPIError` (definite), timeout/5xx → `PayPalUncertainError` |
| `…/paypal/rehearsal.py` | Explicit simulation gateway (`SIM-` ids) |
| `…/services/payments.py` | Order/authorize/capture/refund/reconcile with reserve→call→settle |
| `…/services/webhooks.py` | Verify → record (replay-safe) → re-read resource → reconcile |
| `…/models/` | Identity, payments, webhooks, idempotency, hash-chained audit log |
| `apps/api/migrations/` | Alembic migrations (schema is never created implicitly) |
| `apps/api/scripts/paypal_sandbox_proof.py` | P02 live feasibility gate |
| `apps/web/` | Next.js 16 App Router (Cache Components on). Talks only to the API, server-side |

## Data model (P01/P02)

`organizations`, `users`, `organization_memberships`, `merchant_accounts`,
`payment_orders` 1─1 `payment_authorizations` 1─1 `payment_captures` 1─* `payment_refunds`,
`webhook_events`, `idempotency_records`, `audit_events`.

P03: `agreements` 1─* `agreement_versions` 1─* `acceptance_criteria`; `approvals`
(subject = agreement version or, from P06, capture). `payment_orders.agreement_version_id`
binds a payment to the LOCKED version it pays for; `payment_captures` carries
`approval_id`, `verification_run_id`, `policy_decision_id` for the capture evidence chain.

Planned (P04–P08): `evidence_submissions`, `verification_runs`, `verification_results`,
`policy_decisions`, `refund_cases`.

### Capture evidence chain (target, enforced in P06)

```
payment_capture ─▶ approval (human, capture)            approvals
                ─▶ policy_decision (deterministic)       P06
                ─▶ verification_run (tools)              P04
                ─▶ payment_authorization ─▶ payment_order ─▶ agreement_version (LOCKED)
```

Each payment row keeps our state (`status`), PayPal's last reported status
(`paypal_status`) and the redacted last PayPal response, so the ledger always shows what
PayPal actually said.

## State machines

```
PaymentOrder:  created ──(GET order = APPROVED)──▶ approved ──authorize──▶ authorized
                  └─────────────── cancel ──────────────▶ cancelled / failed

Authorization: authorized ─reserve─▶ capture_in_progress ─▶ captured
                   │   ▲                      │ 4xx
                   │   └──────────────────────┘
                   └──▶ expired / voided / denied / pending

Capture:  requested ─▶ completed | pending ─▶ completed
              │      ─▶ failed  (definite 4xx; retry allowed)
              └────▶ unknown   (timeout/5xx; blocks retries until reconcile)

Refund:   requested ─▶ completed | pending | failed | cancelled | unknown

AgreementVersion: draft ─propose─▶ proposed ─buyer approval─▶ locked ─▶ superseded
                    ▲                 │ withdraw
                    └─────────────────┘        draft/proposed ─discard─▶ discarded
```

## Decisions

| # | Decision | Why |
|---|---|---|
| ADR-1 | Python/FastAPI API + Next.js web, one repo | Per spec; Playwright/Lighthouse worker (P04) shares the Python domain models |
| ADR-2 | Integer minor units | No float rounding anywhere money is stored or compared |
| ADR-3 | reserve → call → settle with deterministic PayPal-Request-Id | DB lock + unique constraints stop local duplicates; PayPal idempotency makes replay safe after a lost response |
| ADR-4 | Webhooks trigger a re-read, never a direct state write | Order-independent, replay-safe, cannot be spoofed by payload contents |
| ADR-5 | Hash-chained audit log in Postgres | Detects out-of-band edits; honestly documented as tamper-*evident*, not tamper-proof |
| ADR-6 | Operator-token gate on `/dev/payments` | P02 needs a driver before real auth/RBAC exists (P06); disabled by default |
| ADR-7 | `packages/shared-schemas` deferred | API OpenAPI schema will be the contract; a generated TS client replaces a hand-shared package (decide in P07) |
| ADR-9 | Immutability in the database, not only in code | A locked agreement is the basis for moving money; triggers make "edit history" impossible even for a buggy code path |
| ADR-10 | Approval quotes a content hash | Binds the human decision to exact content; prevents approve-after-silent-change |
| ADR-11 | Roles from DB on every request, token holds only user id | Immediate revocation; no privilege in client-held data |
| ADR-8 | Next.js 16 (create-next-app default) | Latest stable at build time; Cache Components enabled — dynamic data is streamed inside `<Suspense>` |
