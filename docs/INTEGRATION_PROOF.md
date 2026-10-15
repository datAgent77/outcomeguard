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
| Verification: real Chromium checks (mobile, contact form) | ✅ | ✅ verdict logic | — (real Chromium 156, local) | — |
| Verification: Lighthouse with adaptive retry | ✅ | ✅ | — (real Lighthouse 13.5.0, local + container) | — |
| SSRF policy / egress proxy | ✅ | ✅ + real sockets + real Chromium | — | — |
| Runner network isolation in a container | ✅ | — | — (Docker, `scripts/container_isolation_test.sh`, 17/17 probes) | — |

Last updated: 2026-10-15 (P04) — API suite 205 passed (incl. 4 real-browser tests); Sandbox suite (`-m sandbox`) 2 passed;
full lifecycle run passed against the live PayPal Sandbox (see Runs).

**P02 core gate: PASSED** (OAuth → Create Order → Buyer Approval → Authorize → Capture →
Partial Refund, real Sandbox responses). **Still open:** live webhook delivery and
signature verification, and the lost-response reconcile path (mocks only).

## Verification evidence (P04, 2026-10-15)

All results below were produced by real tools; nothing is simulated. Local runs used
macOS + Chromium 156.0.8078.4 (Chromium sandbox **on**), Lighthouse 13.5.0 on
`chrome-headless-shell`, PostgreSQL 17. Fixtures are served over http://127.0.0.1, so the
demo agreement sets `require_https: false`.

### Broken / fixed demo — [`runs/verification-demo-20261015T123029Z.json`](integration/runs/verification-demo-20261015T123029Z.json)

| Fixture | Verdict | site_available | mobile_layout | contact_form_works | lighthouse_performance | Wall time |
|---|---|---|---|---|---|---|
| landing-page-broken | **FAIL** | pass | pass (390/390 px) | **fail `NO_SUBMISSION_TO_SINK`** (no sink request, 0 receipts, success text not shown) | pass (100, 10.1 s, 1 run) | 25.2 s |
| landing-page-fixed | **PASS** (`verified_pass: true`) | pass | pass | pass (sink 201, 1 receipt in DB, success text shown) | pass (100, 10.2 s, 1 run) | 12.1 s |

Each run stored 5 artifacts (HTTP trace, mobile screenshot, post-submit screenshot,
gzipped Lighthouse report, egress decision log) with SHA-256 hashes listed in the
transcript. Artifacts were downloaded through `GET /verification/artifacts/{id}` as the
buyer approver and re-hashed: match. A user from another organisation got 404.

### End-to-end test (`tests/test_verification_e2e.py`, in CI)

broken → FAIL `NO_SUBMISSION_TO_SINK`; fixed → PASS; exfil → FAIL
`FORM_POSTS_TO_UNAPPROVED_DESTINATION` with the third-party collector receiving **0
requests** (aborted in the browser before transmission).

### Lighthouse adaptive retry (real Lighthouse, `test_lighthouse_adaptive_retry_with_real_lighthouse`)

Threshold 50 vs score 100 → 1 run (≈10.1 s). Threshold 97 (within ±5) → 3 runs
[100, 100, 100] (≈9.9 s each), median decides.

### SSRF / browser isolation on the host (proxy layer)

Real Chromium loading a page whose JavaScript tries 9 vectors (fetch to a local
non-allow-listed port, `[::1]`, `localhost`, decimal IP, 169.254.169.254, 10.0.0.1,
`sendBeacon`, XHR, WebSocket, plus `<img>`/`<iframe>`): the internal canary server
received **0 requests**; the proxy logged `PORT_NOT_ALLOWED`, `METADATA_ADDRESS`,
`PRIVATE_ADDRESS`. Real-socket proxy tests cover CONNECT to internal, Host-header
smuggling, absolute https URIs and DNS rebinding.

### Container isolation — [`runs/container-isolation-20261015T122912Z.json`](integration/runs/container-isolation-20261015T122912Z.json)

Docker 29.5.2 (Docker Desktop, linux/arm64), topology `docker/verifier-isolation.compose.yml`.
Probes run **inside the runner container**:

| Probe | Result |
|---|---|
| Direct TCP to 1.1.1.1:443 / fixture IP | `Network is unreachable` |
| Direct DNS / TCP to `internal-svc` (data network) | name resolution failure |
| Direct HTTP to example.com | name resolution failure |
| Chromium with `--no-proxy-server` (bypass attempt) → fixture / internet | `ERR_ADDRESS_UNREACHABLE` / `ERR_NAME_NOT_RESOLVED` |
| Via proxy → fixture (dev-allow-listed) / example.com | 200 / 200 |
| Via proxy → internal-svc, metadata, gateway, other port, proxy loopback | 403 `DNS_FAILURE`, `METADATA_ADDRESS`, `PRIVATE_ADDRESS`, `PORT_NOT_ALLOWED`, `PORT_NOT_ALLOWED` |
| Real verification job in the container (HTTP + Chromium + Lighthouse via proxy) | 200; 390/390 px; Lighthouse 100 in 9.9 s |
| Worker container → runner job server | 200 with measurement + artifact |

In the container Chromium's own sandbox is **off** (`VERIFIER_CHROMIUM_SANDBOX=0`; no user
namespaces); the container boundary and the internal network are the isolation there.
*(A first container run on 2026-10-15 reported Lighthouse as passing although Lighthouse
had failed to start — the probe returned the error inside its result. The assertion was
tightened to require a score from every tool, the cause (Chromium sandbox in a
container) was fixed, and that run's output was discarded.)*

**Not verified:** the full `docker-compose.yml` stack (API + worker + runner + DB in
containers) end-to-end, and any hosted (Render) environment.

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
