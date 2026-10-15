# Integration proof

This file distinguishes what is merely written from what has been observed working.
Nothing moves to "Tested against PayPal Sandbox" without a transcript in
`docs/integration/runs/` produced by a real run.

Evidence classes — never mixed:
* **Mocked** — unit tests with respx / the scriptable fake gateway. Proves our logic, not PayPal.
* **Simulated** — rehearsal mode (`SIM-` ids). Proves the UI/flow, not PayPal.
* **Real Sandbox** — live calls to `api-m.sandbox.paypal.com`, recorded below.

Legend: ✅ yes · ❌ not yet · — not applicable

| Capability | Implemented | Unit-tested with mocks | Tested against PayPal Sandbox | Tested against real AI provider |
|---|---|---|---|---|
| OAuth client-credentials token + caching | ✅ | ✅ (respx) | ✅ 2026-10-15 | — |
| Create order `intent: AUTHORIZE` | ✅ | ✅ | ✅ 2026-10-15 | — |
| Create-order idempotency (same PayPal-Request-Id → same order) | ✅ | ✅ | ✅ 2026-10-15 (`-m sandbox`) | — |
| Buyer approval read server-side (GET order) | ✅ | ✅ | ✅ 2026-10-15 | — |
| Unapproved order cannot be authorized (PayPal 422) | ✅ | ✅ | ✅ 2026-10-15 (`-m sandbox`) | — |
| Authorize order | ✅ | ✅ | ✅ 2026-10-15 | — |
| Capture authorization | ✅ | ✅ | ✅ 2026-10-15 (single capture; idempotent replay mock-tested only) | — |
| Lost-response reconcile (same PayPal-Request-Id) | ✅ | ✅ | ❌ (not induced live) | — |
| Partial refund | ✅ | ✅ | ✅ 2026-10-15 | — |
| PayPal-reported fee / net breakdowns persisted | ✅ | ✅ | ✅ 2026-10-15 (read back via GET) | — |
| Webhook signature verification | ✅ | ✅ | ❌ (needs public HTTPS URL + webhook id — see [WEBHOOKS.md](WEBHOOKS.md)) | — |
| Rehearsal mode (simulated, labeled) | ✅ | ✅ | — | — |
| Agreement domain (versions, locking, approval, RBAC) | ✅ | ✅ (real Postgres, no external service) | — | — |
| AI criteria / evidence intelligence | ❌ (P05) | ❌ | — | ❌ |
| Playwright / Lighthouse verification | ❌ (P04) | ❌ | — | — |

Last updated: 2026-10-15 — unit suite 101 passed; Sandbox suite (`-m sandbox`) 2 passed;
full lifecycle run passed against the live PayPal Sandbox (see Runs).

**P02 core gate: PASSED** (OAuth → Create Order → Buyer Approval → Authorize → Capture →
Partial Refund, real Sandbox responses). **Still open:** live webhook delivery and
signature verification, and the lost-response reconcile path (mocks only).

## How to reproduce

### Unit tests (mocked PayPal — not proof of live integration)

```bash
cd apps/api
createdb outcomeguard_test   # once
uv run pytest
```

### Opt-in Sandbox suite (unattended: OAuth, create/read order, idempotency)

```bash
cd apps/api
uv run pytest -m sandbox
```

### Full Sandbox gate (needs a human to approve in the browser)

Prerequisites in the PayPal Developer Dashboard (Sandbox):
1. *Apps & Credentials* → Sandbox → create an app → copy Client ID and Secret into the
   repo-root `.env` as `PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET`, with `PAYMENT_MODE=sandbox`.
2. *Testing Tools → Sandbox Accounts*: note the **Personal** (buyer) account login.

```bash
cd apps/api
uv run alembic upgrade head
uv run python scripts/paypal_sandbox_proof.py --amount 10.00 --refund 2.00
```

The script opens PayPal's approval page; log in with the sandbox **Personal** account and
approve. It then authorizes, captures, refunds, re-reads the capture from PayPal, and
writes a transcript to `docs/integration/runs/sandbox-proof-<timestamp>.json`.

## Runs

### 2026-10-15 — full lifecycle, $10.00 authorize / capture, $2.00 partial refund

Transcript: [`runs/sandbox-proof-20261015T104415Z.json`](integration/runs/sandbox-proof-20261015T104415Z.json)
(produced by `scripts/paypal_sandbox_proof.py`; no credentials or tokens).
Buyer approval was given by a human in the browser with the Sandbox **Personal** account
(Visa test card). Merchant: Sandbox Business account of the configured app.

| # | Step | PayPal call | PayPal response (observed) |
|---|---|---|---|
| 1 | OAuth | `POST /v1/oauth2/token` | 200, access token (not recorded) |
| 2 | Create order | `POST /v2/checkout/orders` | order `0X912012XG131200X`, `PAYER_ACTION_REQUIRED`, 10.00 USD, intent `AUTHORIZE` |
| 3 | Buyer approval | browser → `GET /v2/checkout/orders/{id}` | `APPROVED` (read server-side, not from the redirect) |
| 4 | Authorize | `POST /v2/checkout/orders/{id}/authorize` | authorization `4CG20771KK4495022`, `CREATED`, 10.00 USD, expires 2026-11-13T10:57:46Z |
| 5 | Capture | `POST /v2/payments/authorizations/{id}/capture` | capture `65Y23481GC472070C`, `COMPLETED`, 10.00 USD, `final_capture: true` |
| 6 | Partial refund | `POST /v2/payments/captures/{id}/refund` | refund `735071810K223724S`, `COMPLETED`, 2.00 USD |
| 7 | Re-read | `GET /v2/payments/captures/{id}` | `PARTIALLY_REFUNDED`; refundable balance 8.00 USD |
| 8 | Audit | local | 18 events, hash chain intact |

**Independent re-verification** (separate process, straight from PayPal, after the run):
order `COMPLETED`; authorization `CAPTURED`; capture `PARTIALLY_REFUNDED`; refund
`COMPLETED` 2.00 USD. Database rows match.

**Financial breakdown** (all figures PayPal-reported unless marked *derived*):

| Figure | Value | Source |
|---|---|---|
| Gross captured | 10.00 USD | capture `amount` / `seller_receivable_breakdown.gross_amount` |
| PayPal fee on capture | 0.84 USD | capture `seller_receivable_breakdown.paypal_fee` |
| Net to merchant **at capture time** | 9.16 USD | capture `seller_receivable_breakdown.net_amount` (PayPal does not update it after refunds) |
| Refunded to buyer | 2.00 USD | refund `amount` / `seller_payable_breakdown.gross_amount` |
| Fee returned to merchant on refund | 0.00 USD | refund `seller_payable_breakdown.paypal_fee` |
| Debited from merchant for refund | 2.00 USD | refund `seller_payable_breakdown.net_amount` |
| Net merchant proceeds after refund | 7.16 USD — *derived* | 9.16 − 2.00, arithmetic on the two PayPal-reported breakdowns |

The 7.16 USD figure is **not** a balance read from PayPal; the merchant account balance
was not independently verified (that needs the Transaction Search / reporting API, not
used). Fees are never estimated: when PayPal omits a breakdown the API returns `null`.
Rehearsal mode never produces fee figures. *(An earlier report listed "seller net
9.16 USD" without stating it was the at-capture figure; corrected.)*

**Defect found by this run and fixed:** after a capture/refund the parent row's
`paypal_status` was not re-read from PayPal (authorization stayed `CREATED`, capture
stayed `COMPLETED`; our own `status` fields were correct). The service now re-reads the
parent from PayPal after money moves (`_refresh_mirrors`); regression test
`test_paypal_status_mirrors_are_read_back_after_money_moves`. The live rows were
refreshed with read-only GETs and now show `CAPTURED` / `PARTIALLY_REFUNDED` and the
breakdown figures above.
