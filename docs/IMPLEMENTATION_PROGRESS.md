# Implementation progress

Deadline: **2026-11-12 23:00 GMT+3** (PayPal AI Hackathon). Spec: [BUILD_PROMPT_P00.md](BUILD_PROMPT_P00.md).

| Phase | Scope | Status |
|---|---|---|
| P01 | Repository foundation | ✅ done (Docker images not built locally — daemon was off) |
| P02 | PayPal Sandbox proof | ✅ core lifecycle accepted (live Sandbox 2026-10-15) · ⏳ **live webhook verification open** — procedure in WEBHOOKS.md, needs a public URL (approval) |
| P03 | Agreement domain | ✅ accepted (gate re-run before P04: 47/47) |
| P04 | Verification engine (Playwright, Lighthouse, SSRF guard) | ✅ conditionally accepted — see [VERIFICATION.md](VERIFICATION.md), open items in [TECH_DEBT.md](TECH_DEBT.md) |
| P05 | AI contract and evidence intelligence | ✅ accepted — see [AI_VERIFICATION.md](AI_VERIFICATION.md) |
| P06 | Deterministic policy, human approval, auth/RBAC | ✅ accepted (real Sandbox governance run 2026-10-16) — see [GOVERNANCE.md](GOVERNANCE.md) |
| P07 | Product UI + AG Grid ledger | ✅ accepted (real PayPal Sandbox flow through the UI 2026-10-16) — see [P07_UI_VERIFICATION.md](P07_UI_VERIFICATION.md) |
| P08 | Resolution, void and refund governance | ✅ accepted (real Sandbox refund $100 of $500 and void, 2026-10-16) — see [RESOLUTION_AND_REFUNDS.md](RESOLUTION_AND_REFUNDS.md) |
| P09 | Hackathon demo readiness (scope changed from hardening by the user) | 🟡 done, **awaiting review** — see [hackathon/DEMO_READINESS_REPORT.md](hackathon/DEMO_READINESS_REPORT.md) |
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

## P05 — AI contract and evidence intelligence (2026-10-15)

- [x] P05a: provider abstraction, Anthropic Claude (`claude-opus-5`, structured output,
      `fallbacks: "default"`), timeouts/retries/daily token budget, append-only `ai_calls`
- [x] P05b: NL → structured draft; deterministic review (verbatim quotes, no invented
      thresholds/selectors); buyer acceptance with explicit configuration → DRAFT agreement;
      locking still needs propose + approve
- [x] P05c/d: grounded evidence explanations (result id / measurement / artifact refs,
      stored outcomes only, contradiction and payment-claim rejection), separate append-only
      `evidence_analyses`; deterministic fallback when AI is unavailable
- [x] P05e: 49 scripted-provider tests (malformed JSON, unsupported kinds, invented
      selectors, timeouts, outages, refusals, fabricated claims, injection, payment-change
      attempts, no code execution, import isolation) + 2 real-model tests
- [x] Demo with the real model on the real fixtures (broken → FAIL, fixed → PASS, both explained)
- [x] Technical debt register created: [TECH_DEBT.md](TECH_DEBT.md)

### P05 final corrections (2026-10-15)

- [x] Canonical demo keeps accessibility a **blocking** manual review: broken → FAIL, fixed →
      NEEDS_MANUAL_REVIEW, pending authorized human review shown, nothing fabricated
- [x] Default model `claude-sonnet-5-5` (verified via Models API + pricing page), Opus
      selectable, server-side fallback off, actual model ID recorded
- [x] USD spending controls: price table with source/date, atomic worst-case reservation
      under an advisory lock, settlement from usage, fail closed on unknown price/usage
- [x] Stated vs suggested criteria; mandatory requirements stay blocking; suggestions need
      explicit acceptance; weakening only via audited buyer overrides
- [x] Deterministic uncertainty statements and `pending_human_review` in every analysis;
      cross-run references rejected; negation-aware success-claim check
- [x] Two real-model bugs found and fixed with regression tests; all transcripts labeled
      ([runs/README.md](integration/runs/README.md))

## P06 — Deterministic payment governance (2026-10-15)

- [x] Password + server-side session authentication (interim tokens removed), lockout,
      human/service account kinds, `buyer_reviewer` role
- [x] Evidence-bound manual-review decisions; provider self-review and service accounts refused
- [x] Pure policy engine with reason codes; every evaluation stored
- [x] Capture approval bound to authorization + policy inputs hash; consumed once
- [x] Capture re-evaluates under the authorization lock, reserves, commits, then calls PayPal
- [x] DB trigger requires policy/approval/run references on agreement-bound captures;
      service refuses gate-less capture (dev endpoint cannot bypass); prod refuses dev endpoints
- [x] Migration `c3be1fa32c64`; mocked + HTTP + real-Sandbox evidence (see INTEGRATION_PROOF)
- [x] Final integration gate (2026-10-16): real Sandbox order `1TX60210NL069393C` →
      authorization `5JG340237M213032N` → capture `6A555091CM668061A`; human buyer approval
      only, all in-app decisions scripted and labeled; lost capture response reconciled by
      reading the existing capture (1 POST total, same request id)
- [x] Reconcile is read-first (find the capture on the PayPal order before replaying the same
      `PayPal-Request-Id`); demo polling bounded (timeout, transient-failure budget)
- [x] Access logs redact the query string of `/paypal/*` and `/rehearsal/*` (order token, PayerID)

## P07 — Product UI (2026-10-16)

- [x] Same-origin BFF: HttpOnly session cookie, CSRF (Origin + custom header), path allow-list, 401 → re-login
- [x] Landing page, login/logout, role-aware navigation, buyer dashboard, provider workspace
- [x] AI agreement wizard (stated vs suggested, opt-in, blocking protection, configuration, propose + approve/lock)
- [x] Agreement workspace with six tabs; separate agreement / verification / payment statuses
- [x] Delivery submission with bounded polling; evidence report with real measurements and artifact viewers (screenshot, gzip Lighthouse JSON, HTTP trace, egress log, form-sink receipts); AI explanation labelled as interpretation
- [x] Manual-review panel (authorized buyer reviewers, rationale + attestation + confirmation, stale-evidence rejection, scripted decisions labelled)
- [x] Payment workspace: Sandbox checkout (idempotent order creation), bounded approval polling, authorize, live policy with plain-English reasons, capture approval and capture as separate confirmed actions, UNKNOWN + guarded reconcile
- [x] AG Grid Community ledger (server search/filters, sort, pagination, drawer, timeline on mobile) + per-agreement activity timeline
- [x] Read-only API additions + `accept_suggestions` route fix + payment `Idempotency-Key` (tests: `test_workspace_api.py`)
- [x] Browser tests: 36 mocked (CI) incl. axe and 5 viewport widths; 8-step real-API flow (rehearsal payments)
- [x] Real PayPal Sandbox flow through the UI, performed by a person (order `3PE754440H8280847` → capture `70F43933LT884351X`)

## P08 — Resolution, void and refund governance (2026-10-16)

- [x] Plan before code: `docs/architecture/P08_RESOLUTION_PLAN.md`
- [x] Migration `9884007a0cc7`: cases, events, proposals, approvals, financial policy decisions, voids; refund governance columns; triggers (append-only, immutable snapshot/proposal content, approval side/hash check, refund and void governance); up/down/up verified
- [x] Pure refund/void policy with reason codes and case state machine
- [x] Refunds only through an agreed two-sided proposal + fresh policy under the capture lock; P02 operator bypass closed (service + trigger, regression tests)
- [x] Read-first refund reconciliation (PayPal refund id → order refunds → same-key replay within 6 h → operator)
- [x] Governed void under the authorization lock, capture/void mutual exclusion, provider consent once delivery is verified, VOIDED webhook
- [x] API, ledger and overview extensions; Resolution tab, case wizard + workspace, refund confirmation, UNKNOWN reconciliation, Cancel authorization
- [x] Tests: 78 backend (pure, mocked fault injection, HTTP), 9 mocked browser, 7-step real-API browser flow with simulated payments
- [x] Real PayPal Sandbox: resolution-governed partial refund (capture `95Y56941P3956690M` → refund `2DF34389JE394200F`, $400 remaining) and separate authorization void (`32284282MB448210R` VOIDED)

## Verified vs unverified

See [INTEGRATION_PROOF.md](INTEGRATION_PROOF.md).
