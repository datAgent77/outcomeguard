# Integration proof

This file distinguishes what is merely written from what has been observed working.
Nothing moves to "Tested against PayPal Sandbox" without a transcript in
`docs/integration/runs/` produced by a real run.

Legend: ✅ yes · ❌ not yet · — not applicable

| Capability | Implemented | Unit-tested with mocks | Tested against PayPal Sandbox | Tested against real AI provider |
|---|---|---|---|---|
| OAuth client-credentials token + caching | ✅ | ✅ (respx) | ❌ | — |
| Create order `intent: AUTHORIZE` | ✅ | ✅ | ❌ | — |
| Buyer approval read server-side (GET order) | ✅ | ✅ | ❌ | — |
| Authorize order | ✅ | ✅ | ❌ | — |
| Capture authorization (idempotent) | ✅ | ✅ | ❌ | — |
| Lost-response reconcile (same PayPal-Request-Id) | ✅ | ✅ | ❌ | — |
| Partial refund | ✅ | ✅ | ❌ | — |
| Webhook signature verification | ✅ | ✅ | ❌ (needs public HTTPS URL + webhook id) | — |
| Rehearsal mode (simulated, labeled) | ✅ | ✅ | — | — |
| Agreement domain (versions, locking, approval, RBAC) | ✅ | ✅ (real Postgres, no external service) | — | — |
| AI criteria / evidence intelligence | ❌ (P05) | ❌ | — | ❌ |
| Playwright / Lighthouse verification | ❌ (P04) | ❌ | — | — |

Last updated: 2026-10-15 (P03) — unit suite 97 passed; Sandbox suite skipped (no credentials).

**P02 status: BLOCKED — real Sandbox verification pending.** No row above is ✅ in the
"Tested against PayPal Sandbox" column until a run transcript exists below.

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
uv run python scripts/paypal_sandbox_proof.py --amount 500.00 --refund 50.00
```

The script opens PayPal's approval page; log in with the sandbox **Personal** account and
approve. It then authorizes, captures, refunds $50, re-reads the capture from PayPal, and
writes a transcript to `docs/integration/runs/sandbox-proof-<timestamp>.json`.

## Runs

*(none yet)*
