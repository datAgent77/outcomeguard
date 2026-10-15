# Implementation progress

Deadline: **2026-11-12 23:00 GMT+3** (PayPal AI Hackathon). Spec: [BUILD_PROMPT_P00.md](BUILD_PROMPT_P00.md).

| Phase | Scope | Status |
|---|---|---|
| P01 | Repository foundation | ✅ done (Docker images not built locally — daemon was off) |
| P02 | PayPal Sandbox proof | ✅ core lifecycle accepted (live Sandbox 2026-10-15) · ⏳ **live webhook verification open** — procedure in WEBHOOKS.md, needs a public URL (approval) |
| P03 | Agreement domain | ✅ accepted (gate re-run before P04: 47/47) |
| P04 | Verification engine (Playwright, Lighthouse, SSRF guard) | 🟡 implemented + tested; **awaiting review** — see [VERIFICATION.md](VERIFICATION.md) |
| P05 | AI contract and evidence intelligence | ⏳ |
| P06 | Deterministic policy, human approval, auth/RBAC | ⏳ |
| P07 | Product UI + AG Grid ledger | ⏳ |
| P08 | Resolution and refunds | ⏳ (payment-level refund done in P02) |
| P09 | Security and integration hardening | ⏳ |
| P10 | Hackathon delivery | ⏳ |

## P01 — Repository foundation (2026-10-15)

- [x] Monorepo: `apps/api` (FastAPI, uv, Python 3.12), `apps/web` (Next.js 16, TS strict, Tailwind 4)
- [x] Config from env, Sandbox-only guard, `postgres://` → `postgresql+psycopg://` normalisation
- [x] JSON logging with secret redaction
- [x] SQLAlchemy 2 models + first Alembic migration (12 tables)
- [x] `/health`, `/ready` (DB ping + non-secret config), `/config/public`
- [x] Dockerfiles, `docker-compose.yml`, `render.yaml` (not deployed), GitHub Actions CI
- [x] `.env.example`, `.gitignore`, docs skeleton
- [ ] `docker compose up` verified (Docker daemon not running on dev machine)
- [x] CI green on GitHub for commit `6da634e` (api + web jobs)

## P02 — PayPal Sandbox proof (2026-10-15)

- [x] HTTPX gateway: OAuth caching, PayPal-Request-Id, definite vs uncertain errors
- [x] Orders v2 create/get/authorize; Payments v2 get/capture/void/refund; webhook verify
- [x] Reserve → call → settle; UNKNOWN + reconcile; payee pinning; amount checks
- [x] Webhook intake: verify, replay-safe store, re-read + reconcile, no regression
- [x] Hash-chained audit log
- [x] Explicit rehearsal mode
- [x] Operator-gated `/dev/payments` driver + `scripts/paypal_sandbox_proof.py`
- [x] 50 unit tests (mocked PayPal), including concurrency
- [x] HTTP smoke test in rehearsal mode (order → simulated approval → authorize → capture → duplicate blocked → $50 refund → audit chain intact)
- [x] **Live Sandbox run 2026-10-15**: $10 authorize → capture → $2 refund, all `COMPLETED` (see INTEGRATION_PROOF.md)
- [x] `-m sandbox` suite: 2 passed (create/read order, idempotency, unapproved authorize → 422)
- [x] Defect from live run fixed: parent `paypal_status` re-read after capture/refund
- [x] Financial reporting: PayPal-reported fee/net breakdowns persisted (migration `3cb04b42a4ff`); API `settlement` block separates gross, PayPal fee, net at capture, refunds, fees returned, merchant debits and a clearly *derived* net-after-refunds; nothing estimated
- [x] Webhook go-live procedure + `scripts/webhook_report.py` ([WEBHOOKS.md](WEBHOOKS.md)) — not executed
- [ ] Live webhook delivery — needs a public HTTPS URL (Render deploy) and `PAYPAL_WEBHOOK_ID`

## P03 — Agreement domain (2026-10-15)

- [x] `agreements`, `agreement_versions`, `acceptance_criteria`, `approvals` (migration `686de0e0f441`)
- [x] Typed, strictly validated verification specs: `http_availability`, `mobile_viewport`,
      `contact_form` (posts only to a named operator sink), `lighthouse_performance`,
      `manual_review` (→ `MANUAL_REVIEW_REQUIRED`)
- [x] Deterministic landing-page template (not AI)
- [x] Version states draft → proposed → locked → superseded (+ withdraw, discard)
- [x] Buyer approval bound to the SHA-256 `content_hash` the approver saw; approval locks
- [x] Immutability enforced twice: service layer and Postgres triggers (locked/superseded
      versions, their criteria, and approvals are append-only)
- [x] Optimistic concurrency (`revision`, `expected_revision`) + agreement row lock
- [x] RBAC: buyer_admin edits, buyer_approver/buyer_admin approve, merchant reads only,
      other organisations get 404; roles loaded from DB per request
- [x] Interim signed session tokens (operator-gated `/dev/session`) — real login in P06
- [x] Payment binding: `payment_orders.agreement_version_id`; product orders only for
      LOCKED versions with amount + merchant taken from the version.
      `payment_captures.approval_id` (FK), `verification_run_id`, `policy_decision_id`
      columns reserved — FKs and enforcement land with P04/P06
- [x] Audit event for every material change; rejected attempts leave no trace or partial write
- [x] 47 new tests (97 total at P03; 101 after P02 financial fixes), migration up/down/up verified, `alembic check` clean

## P04 — Evidence verification (2026-10-15)

- [x] P03 gate re-run first: 47/47 P03 tests, full suite green, no open P03 defects
- [x] P04a: evidence_submissions, verification_runs/results/jobs, evidence_artifacts,
      form_sink_receipts (migration `d35b10aa71f2`) + immutability triggers; Postgres
      queue (SKIP LOCKED, leases, max attempts); pure verdict engine
- [x] P04b: single SSRF policy (`netpolicy.py`), egress guard proxy with IP pinning,
      HTTP check, content-addressed evidence store with hash-verified reads
- [x] P04c: real Chromium mobile + contact-form checks, controlled sink (HMAC run token,
      limits, hashed values), browser-level blocking of unapproved destinations
- [x] P04d: Lighthouse 13.5.0 via chrome-headless-shell, 1 run, +2 when within ±5 of the
      threshold, median of 3
- [x] P04e: real fixtures (broken / fixed / exfil), end-to-end test and demo transcript
- [x] Credential isolation: verifier never loads PayPal settings; runner env allow-listed
- [x] Container topology (`docker/verifier-isolation.compose.yml`) — see INTEGRATION_PROOF
- [x] PayPal Sandbox suite still green after P04 (2/2)

## Verified vs unverified

See [INTEGRATION_PROOF.md](INTEGRATION_PROOF.md).
