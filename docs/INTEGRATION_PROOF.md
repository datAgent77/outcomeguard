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
| AI agreement drafting (structured output + deterministic review) | ✅ | ✅ (ScriptedProvider) | — | ✅ 2026-10-15 `claude-sonnet-5-5` (also `claude-opus-5`) |
| AI evidence explanations (grounded) | ✅ | ✅ | — | ✅ 2026-10-15 `claude-sonnet-5-5` (also `claude-opus-5`) |
| AI USD budget reservation / settlement | ✅ | ✅ (incl. concurrency) | — | ✅ settled from real usage (6 calls) |
| Prompt-injection containment | ✅ | ✅ (model that obeys the injection) | — | ✅ 2026-10-15 (real model did not follow it) |
| Verification: real Chromium checks (mobile, contact form) | ✅ | ✅ verdict logic | — (real Chromium 156, local) | — |
| Verification: Lighthouse with adaptive retry | ✅ | ✅ | — (real Lighthouse 13.5.0, local + container) | — |
| SSRF policy / egress proxy | ✅ | ✅ + real sockets + real Chromium | — | — |
| Runner network isolation in a container | ✅ | — | — (Docker, `scripts/container_isolation_test.sh`, 17/17 probes) | — |

Last updated: 2026-10-16 (P08, partial) — API suite 341 passed (incl. 4 real-browser tests); `-m ai_live` 2 passed; `-m sandbox` 2 passed; `mypy` clean; web: lint, typecheck, build clean; 36 mocked browser tests + 8-step real-API browser flow passed; P06 governance run completed against the live PayPal Sandbox;
full lifecycle run passed against the live PayPal Sandbox (see Runs).

**P02 core gate: PASSED** (OAuth → Create Order → Buyer Approval → Authorize → Capture →
Partial Refund, real Sandbox responses). **Still open:** live webhook delivery and
signature verification. (The lost-response reconcile path was proven on real Sandbox in P06.)

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

## AI evidence (P05, 2026-10-15)

Real calls to the Anthropic Claude API through the official SDK (`anthropic` 1.14.0).
Every call is recorded in `ai_calls` (no prompts or keys). Costs are **local estimates**
from reported usage × the published price list — not Anthropic billing.

### Final configuration (canonical)

`claude-sonnet-5-5`, effort `high`, **server-side fallback off**, `max_tokens` 12 000,
1 retry, daily budget $5.00 (estimated). Every answer below came from the requested model
(`model_used == model_requested`).

| Real call (Sonnet 5.5) | Latency | Tokens in/out | Est. cost | Reserved | Result |
|---|---|---|---|---|---|
| Live test: draft | 12.4 s | 2 778 / 1 361 | $0.019 | $0.258 | 4 stated (accessibility **blocking** manual review, Lighthouse 90), http availability as **suggestion** |
| Live test: injection in page data | 13.8 s | — | — | — | `grounded`; contact form stayed `fail`; model described the injected text as evidence, did not follow it |
| Canonical demo 1: draft / broken / fixed | 9.8 / 15.6 / 12.4 s | 2 778/1 441 · 4 232/2 371 · 4 205/2 074 | $0.020 · $0.032 · $0.029 | $0.26 each | fixed summary wrongly replaced — finding 2 |
| **Canonical demo 2**: draft / broken / fixed | 10.2 / 16.2 / 14.7 s | 2 778/1 551 · 4 239/2 448 · 4 235/2 246 | $0.021 · $0.033 · $0.031 | $0.26 each | broken `grounded`, fixed `grounded`, 0 rejected claims |

Sonnet 5.5 demo calls: 22 467 in / 12 131 out tokens, **≈ $0.166 estimated** (reserved
worst case $1.57, all settled from usage). Earlier `claude-opus-5` runs (first P05 round,
fallback on): 21 485 in / 16 663 out ≈ $0.52 at list price.

### Canonical demo — [`runs/ai-demo-canonical-20261015T134532Z.json`](integration/runs/ai-demo-canonical-20261015T134532Z.json)

1. Request: *"Build a mobile-friendly landing page for $500. The contact form must work, the
   website must be accessible and Lighthouse performance must score at least 90."*
2. AI draft: **stated** — `mobile_viewport` ("Build a mobile-friendly landing page"),
   `contact_form` ("The contact form must work"), `manual_review` accessibility ("the
   website must be accessible", **blocking**), `lighthouse_performance` (min **90**, from
   the text); **suggested** — `http_availability` (rationale given). Ambiguities: Lighthouse
   form factor, accessibility standard, delivery URL, form success definition, …
3. Buyer review (scripted human decisions): standard test-form fields opted in; the
   availability **suggestion explicitly accepted**; HTTPS relaxed for local http fixtures;
   **no blocking override**.
4. Locked: propose + approval by the buyer approver (content hash bound).
5. Broken page → **FAIL** (`contact_form_works` fail `NO_SUBMISSION_TO_SINK`; accessibility
   `manual_review_required`). AI: form found and filled, `sink_requests` empty,
   `sink_receipts_in_db` 0, success text not seen; root cause not visible in the
   measurements; remediation for the submit handler. Accessibility marked unresolved.
6. Fixed page → **NEEDS_MANUAL_REVIEW** (`verified_pass: false`): 4 automated criteria pass
   (sink 201, 1 receipt, success text, Lighthouse 100); accessibility unresolved. AI: "The
   work should therefore not be considered fully verified."
7. `pending_human_review`: `website_accessible` — *awaiting authorized human review*,
   `decision: null`. No human decision fabricated, verdict unchanged, no payment action.

### Findings from real-model runs (both fixed, both transcripts kept)

1. **Grounding validator too strict** (`ai-demo-20261015T131618Z.json`): references to a
   result's own `measured` field (empty for manual review) were rejected. Fixed;
   regression tests `test_refs_to_real_top_level_result_fields_are_grounded`,
   `test_measured_field_reference_regression`.
2. **Success-claim check ignored negation** (`ai-demo-canonical-20261015T134325Z.json`):
   "should not be considered fully verified" was treated as a success claim and the
   correct summary replaced. Fixed with clause-level negation detection; tests
   `test_summary_success_claims_respect_negation` (negated accepted, real claims rejected).

### Alternative scenario (not canonical)

[`runs/ai-demo-20261015T131921Z.json`](integration/runs/ai-demo-20261015T131921Z.json):
`claude-opus-5`, the buyer **explicitly made accessibility non-blocking** (audited), so the
fixed page was PASS. Valid only as a buyer-approved relaxed-criteria scenario.

## Payment governance evidence (P06, 2026-10-15 / real Sandbox 2026-10-16)

| Scenario | Mocked gateway (`tests/test_governance.py`) | HTTP + sessions, simulated payments (`tests/test_governance_api.py`) | Real PayPal Sandbox (`scripts/governance_demo.py`) |
|---|---|---|---|
| Broken page → FAIL → capture blocked | ✅ | — | ✅ |
| Fixed page → NEEDS_MANUAL_REVIEW → capture blocked | ✅ | ✅ | ✅ |
| Manual review satisfies accessibility | ✅ | ✅ | ✅ mechanism only — **scripted demo account, no human review** |
| Separate buyer capture approval bound to inputs hash | ✅ | ✅ | ✅ (scripted demo account) |
| Fresh policy evaluation at capture (under lock) | ✅ | ✅ | ✅ |
| Capture completes | ✅ (mock) | ✅ (simulated, labeled) | ✅ real Sandbox capture |
| Duplicate capture blocked | ✅ | ✅ | ✅ |
| Stale evidence / agreement change blocks capture | ✅ / ✅ | — | ✅ stale evidence / — |
| Provider self-approval rejected | ✅ | ✅ | ✅ |
| AI / service-account approval rejected | ✅ | — | ✅ |
| Expired authorization rejected | ✅ | — | — (cannot be produced live; mock only) |
| Lost PayPal response → UNKNOWN → reconcile | ✅ | — | ✅ (real capture POST, response dropped client-side) |
| Dev endpoint cannot bypass policy | ✅ | ✅ | — |
| DB trigger rejects capture rows without policy refs | ✅ | — | — |

### Real Sandbox governance run (2026-10-16)

Transcript: [`runs/governance-demo-20261016T071243Z.json`](integration/runs/governance-demo-20261016T071243Z.json)
(status `completed`, 07:12:43 → 07:14:22 UTC, 98.8 s). It contains no approval URL, buyer
token, PayerID or credentials.

**Who did what.** Exactly one step was a human action: the buyer approving the payment in
the PayPal Sandbox browser. Every OutcomeGuard user action (lock agreement, submit evidence,
manual review, capture approval, capture request) was performed **by the script through demo
accounts**. The manual-review rationale stored in the database says so literally:
*"SCRIPTED DEMO DECISION … No human performed an accessibility review."* This run proves the
mechanism, not a real accessibility review. The only simulation: the capture response was
discarded client-side after a real POST.

PayPal Sandbox objects:

| Object | ID | Final PayPal state |
|---|---|---|
| Order (intent AUTHORIZE, $500.00 USD) | `1TX60210NL069393C` | `COMPLETED`, 1 capture |
| Authorization | `5JG340237M213032N` | `CAPTURED` (expiry 2026-11-14 07:13:28 UTC) |
| Capture | `6A555091CM668061A` | `COMPLETED`, $500.00, `final_capture: true`, invoice `og_a5283f0af8b245ac82c53ddf` |

PayPal's seller receivable breakdown for the capture: gross 500.00, PayPal fee 17.94, net
482.06 USD (as reported by PayPal; no fee rate is assumed).

| t (s) | Step | Actor | Result |
|---|---|---|---|
| 0.2 | Agreement locked (5 blocking criteria, content hash `06ccf0ae…`) | SCRIPTED | locked |
| 2.6 | Order created | SYSTEM | `PAYER_ACTION_REQUIRED` |
| 43.5 | Buyer approval detected via server-side `GET order` | **HUMAN** | `APPROVED`, 0 transient poll failures |
| 45.6 | Authorize | SYSTEM | DB `authorized` |
| 68.2 | Broken page verified (run `c5e434b8…`, snapshot `e73264fa…`) | SCRIPTED submit, SYSTEM verify | FAIL → policy `blocked` [`BLOCKING_CRITERION_FAILED`, `MANUAL_REVIEW_PENDING`] |
| 68.3 | Capture approval attempt | SCRIPTED | `POLICY_NOT_SATISFIED` |
| 80.0 | Fixed page verified (run `8237f0af…`, snapshot `74280b86…`) | SCRIPTED submit, SYSTEM verify | NEEDS_MANUAL_REVIEW → `needs_review`; approval attempt `POLICY_NOT_SATISFIED` |
| 80.0 | Review by provider (maya) / by service account (ai-assistant) | SCRIPTED | both rejected (provider; human account required) |
| 80.0 | Review `5babc739…` bound to snapshot `74280b86…` | SCRIPTED (rita) | `eligible_for_approval`, inputs `9aa94b6e…` |
| 80.0 | Capture approval `ee416674…` | SCRIPTED (bob) | bound to `9aa94b6e…` |
| 80.7 | New evidence submitted, capture with that approval | SCRIPTED | `STALE_EVIDENCE` |
| 92.3 | New run `c57b4991…` (snapshot `d5e3cc67…`) | SYSTEM | `needs_review` — old review is bound to the old run |
| 92.3 | Review `145ea891…` | SCRIPTED (rita) | `eligible_for_approval`, inputs `eaac5125…` |
| 92.9 | Capture with old approval | SCRIPTED | `APPROVAL_STALE` |
| 92.9 | Fresh approval `b37f601d…` | SCRIPTED (bob) | bound to `eaac5125…` |
| 94.8 | Capture: reserve (policy decision `15c6868a…`, approval consumed) → 1 real POST, response dropped | SCRIPTED request, SYSTEM call | DB capture `unknown`, authorization `capture_in_progress` |
| 95.3 | Second capture while UNKNOWN | SCRIPTED | `CAPTURE_IN_PROGRESS` |
| 98.3 | Reconcile | SYSTEM | method `read_existing_capture`; DB capture `completed`, authorization `captured` |
| 98.8 | Duplicate capture / new approval | SCRIPTED | `ALREADY_CAPTURED` / `POLICY_NOT_SATISFIED` [`AUTH_NOT_CAPTURABLE`, `ALREADY_CAPTURED`] |

**Lost response, no second capture.** The capture used `PayPal-Request-Id`
`og-cap-47cafc34-2d9e-4c98-a4cd-e9193089eb38-c34ac36e`. Exactly one capture POST was sent in
the whole run (`capture_posts_sent: 1` before and after reconcile). Reconcile first reads the
order from PayPal and matches the existing capture by invoice id/authorization link; it only
replays the **same** request id if PayPal shows no capture. Here it found the capture and
issued no further POST. PayPal's order shows exactly one capture.

The database policy trail ends with `capture / capture_approved` and
`settlement / captured (PAYPAL_CAPTURE_COMPLETED)`, both under inputs hash `eaac5125…`, the
hash the approver approved. The order row remains `authorized`: order, authorization and
capture state are tracked on separate rows (authorization `captured`, capture `completed`).

Earlier attempts (2026-10-15): orders `0J671308PH8748725` and `3XF69583SJ739070F` stopped
before authorization (transient `502` while polling; approval timeout). Both are verified
unauthorized and uncaptured in the DB (0 authorizations, 0 captures); PayPal now returns 404
for them (unapproved orders expired). Their approval links were not reused.

## Product UI evidence (P07, 2026-10-16)

| Scenario | Mocked API (browser, CI) | Real API, simulated payments (browser) | Real PayPal Sandbox (browser) |
|---|---|---|---|
| Login / logout / expired session | ✅ | ✅ login + role switching | — |
| Role-aware navigation and actions | ✅ | ✅ (provider has no review action) | — |
| AI wizard → review → configure → propose → lock | ✅ (canned draft) | ✅ real `claude-sonnet-5-5` draft | ✅ preparation run (real model, Sandbox-mode API) |
| Broken page → FAIL | — | ✅ real Chromium + Lighthouse | — |
| Fixed page → NEEDS_MANUAL_REVIEW, artifacts from evidence store | ✅ | ✅ | ✅ preparation run |
| Manual review (authorized account, attestation, confirmation) | ✅ incl. stale evidence | ✅ **scripted test decision, labelled** | ✅ preparation run, **scripted, labelled** |
| Checkout → buyer approval → authorize | ✅ | ✅ SIMULATED (rehearsal page) | ✅ person in the UI + PayPal Sandbox |
| Policy blocks capture (review pending / already captured) | ✅ | ✅ | ✅ already captured |
| Capture approval + capture as separate confirmed actions | ✅ | ✅ SIMULATED | ✅ real Sandbox capture |
| UNKNOWN outcome → guarded reconcile, no retry | ✅ | — | — (proven at API level in P06) |
| Ledger + activity timeline from persisted events | ✅ | ✅ | — |
| Layout 1440 / 1280 / 768 / 390 / 375 px | ✅ | ✅ 1440 + 390 | — |
| axe-core WCAG 2 A/AA (no serious/critical) | ✅ | — | — |

### Real PayPal Sandbox through the UI (2026-10-16)

Agreement `68e67b4c-3105-483a-85b3-e587b23adc79`, prepared by `sandbox-prep.spec.ts`
(real AI draft, locked, fixed page verified → NEEDS_MANUAL_REVIEW). **The accessibility
review in this run is a scripted demo decision** (rita@acme.demo, rationale starts with
`SCRIPTED DEMO DECISION`). Every payment step was then performed **by a person in the
web UI** (signed in as the demo buyer admin alice@acme.demo), including the buyer
approval on PayPal's Sandbox page. No script touched the payment.

| t (UTC) | Event (audit log) | Actor |
|---|---|---|
| 08:28:07 | order requested | alice@acme.demo (UI, person) |
| 08:28:09 | PayPal order created | system |
| 08:28:32 | buyer approved at PayPal (read server-side) | person at PayPal Sandbox |
| 08:28:52 | authorization created | alice@acme.demo (UI, person) |
| 08:29:06 | policy `eligible_for_approval` → capture approval `eb7902f6…` bound to inputs `770894d9…` | alice@acme.demo (UI, person) |
| 08:31:16 | capture-time policy `capture_approved` (decision `4059050a…`), capture requested | alice@acme.demo (UI, person) |
| 08:31:17 | capture completed | system |

PayPal Sandbox objects, re-read from PayPal after the run:

| Object | ID | PayPal state |
|---|---|---|
| Order ($500.00 USD, AUTHORIZE) | `3PE754440H8280847` | `COMPLETED`, one authorization, one capture |
| Authorization | `7HU39463K8073954H` | `CAPTURED` |
| Capture | `70F43933LT884351X` | `COMPLETED`, $500.00, `final_capture: true`, invoice `og_f99b29d277154e3e90d98e6b`; PayPal fee 17.94, net 482.06 (PayPal-reported) |

DB: capture `completed` (request id `og-cap-ba4e06f3-…`), authorization `captured`,
approval consumed by the capture; the payment tab then shows the fresh policy blocked with
`AUTH_NOT_CAPTURABLE` + `ALREADY_CAPTURED` and no capture action. Audit hash chain
verified intact (`verify_chain` → ok).

Details, screenshots and commands: [P07_UI_VERIFICATION.md](P07_UI_VERIFICATION.md). The
simulated-payment run made no PayPal call; its order/authorization/capture ids start with
`SIM-` and every screen and ledger row marks them SIMULATED.

## Resolution, void and refund evidence (P08, 2026-10-16)

| Scenario | Mocked PayPal (service tests) | HTTP, simulated | Browser, real API, simulated | Real PayPal Sandbox |
|---|---|---|---|---|
| Open case bound to agreement + evidence snapshot | ✅ | ✅ | ✅ | ⏳ pending READY |
| Negotiate, two-sided approval, stale/missing approval blocked | ✅ | ✅ | ✅ | ⏳ |
| Partial refund executed through the case | ✅ | ✅ | ✅ | ⏳ |
| Full refund / multiple partials / over-refund / wrong currency | ✅ | — | — | — |
| Duplicate + concurrent refund safe | ✅ | ✅ (same key) | ✅ (double click) | — |
| PayPal 5xx, lost response → UNKNOWN → read-first reconcile | ✅ | — | ✅ mocked UI | — |
| No automatic re-send: not found at PayPal → reconciliation_required, later read settles | ✅ | — | — | — |
| PayPal-reported refund figures, nothing invented | ✅ | — | — | ⏳ |
| Void: valid, idempotent, unauthorized, captured, UNKNOWN capture, timeout, race with capture | ✅ | ✅ (valid, idempotent, roles) | ✅ (valid) | ⏳ |
| Ledger shows case + refund/void trail | — | ✅ | ✅ | ⏳ |

Details: [RESOLUTION_AND_REFUNDS.md](RESOLUTION_AND_REFUNDS.md). Screenshots from the running
app with simulated payments: `docs/p08/screenshots/`. **No real Sandbox order, refund or void
was created in P08 so far** — the demonstration waits for the user's READY.

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
