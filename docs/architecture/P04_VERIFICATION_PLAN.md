# P04 — Evidence verification worker: implementation plan

> **Status: proposal for review. Not implemented.** Implementation starts only after the
> P03 review checkpoint.

## Goals

Run the acceptance criteria of a **locked** agreement version against the provider's
delivered site with real tools, store what was measured as evidence, and produce a
deterministic verdict that the P06 policy engine can consume. No AI in this phase; P05
will only *read* these results.

## Components

```
 provider ──POST /agreements/{id}/evidence──▶ API ──insert──▶ evidence_submissions
                                               │               verification_runs (queued)
                                               ▼
                                    Postgres job queue (SKIP LOCKED)
                                               │
                         ┌─────────────────────▼──────────────────────┐
                         │ verifier worker (separate process/container)│
                         │  orchestrator ── evaluators (pure functions)│
                         │      │                                      │
                         │      ├─ HTTP check  (httpx + GuardedTransport)
                         │      ├─ Playwright Chromium ─┐              │
                         │      └─ Lighthouse CLI ──────┤              │
                         │                  egress guard proxy ───────┼──▶ internet only
                         └────────────────────────────────────────────┘
                                               │ results + artifacts (sha256)
                                               ▼
                                verification_results, evidence_artifacts
```

* The worker is the same Python package (`outcomeguard.verification`) started as
  `python -m outcomeguard.verification.worker` in its **own container image** (Playwright
  Chromium + Node Lighthouse). It shares models with the API but runs with a separate
  database role (insert/update on verification tables only) and no PayPal credentials.
* **Postgres-backed queue** (`verification_jobs`): `SELECT … FOR UPDATE SKIP LOCKED`,
  lease + heartbeat, `max_attempts`, exponential backoff; a crashed worker's lease expires
  and the job is retried. No Redis needed.

## Data model (migration P04)

| Table | Key fields |
|---|---|
| `evidence_submissions` | agreement_version_id (must be LOCKED), submitted_by, delivery_url, repo_url, commit_sha, notes, `submission_hash` (sha256 of canonical submission), created_at — append-only |
| `evidence_artifacts` | submission_id / run_id, kind (`screenshot`, `lighthouse_json`, `http_trace`, `dom_snapshot`, `upload`), sha256, size, content_type, storage_key — append-only |
| `verification_runs` | submission_id, agreement_version_id, version `content_hash` copied in, status (`queued`→`running`→`completed`/`errored`/`timed_out`), attempt, worker_id, tool versions (Chromium, Playwright, Lighthouse), config hash, started/finished, `verdict` |
| `verification_results` | run_id, criterion_id, outcome (`PASS`/`FAIL`/`ERROR`/`NOT_RUN`/`MANUAL_REVIEW_REQUIRED`), measured (JSONB), expected (JSONB from the spec), reason code, artifact ids |
| `verification_jobs` | run_id, state, lease_until, attempts, last_error |

Completed runs and their results become immutable via triggers (same pattern as P03).
`payment_captures.verification_run_id` gets its FK here.

## Checks

| Criterion kind | Tool | Measured | Pass rule (deterministic) |
|---|---|---|---|
| `http_availability` | httpx through `GuardedTransport` | final status, redirect chain, TLS, latency | final status ∈ `expected_status`, redirects ≤ `max_redirects`, HTTPS if `require_https` |
| `mobile_viewport` | Playwright Chromium, viewport from spec, touch + mobile UA | `scrollWidth` vs `clientWidth`, visibility of each selector, screenshot | no horizontal overflow (≤ 1 px tolerance) and every selector visible |
| `contact_form` | Playwright | fields filled, submit clicked, network requests, page text after submit, sink receipt | success text appears **and** the configured sink recorded a submission carrying the run's nonce |
| `lighthouse_performance` | Lighthouse CLI, `--only-categories=performance --form-factor=mobile --throttling-method=simulate`, N runs | each score, **median** | median ≥ `min_score` |
| `manual_review` | — | — | always `MANUAL_REVIEW_REQUIRED` |

**Run verdict** (pure function, unit-tested exhaustively):
`FAIL` if any blocking criterion FAILs → else `INCONCLUSIVE` if any blocking ERROR/NOT_RUN
→ else `NEEDS_MANUAL_REVIEW` if any blocking MANUAL_REVIEW_REQUIRED → else `PASS`.
Non-blocking criteria are reported but never change the verdict.

### Safe contact-form testing

* Each agreement's contact-form spec names a sink (`test_endpoint_ref`); sinks are
  configured by the operator (`VERIFIER_FORM_SINKS`), never supplied by the provider.
* OutcomeGuard hosts the sink: `POST /sinks/{ref}/{run_nonce}` records the field names and
  a hash of values — no free-form data is stored.
* Playwright intercepts all non-GET requests during the form step: requests to the
  configured sink pass; requests to any other destination are **blocked and reported**
  (`FORM_POSTS_TO_UNCONFIGURED_ENDPOINT` → FAIL or MANUAL_REVIEW per spec), so a
  verification run can never send data to an arbitrary third party.
* Field values are fixed test data from the spec (e.g. `verifier@example.com`).
* Limitation: a real production form that posts to its own backend must expose a
  verification mode that targets the sink; otherwise the criterion is manual review.

## SSRF and sandboxing

1. **URL validation:** `https` (http only if the spec allows), ports 443/80, no userinfo,
   IDNA-normalised host, no IP literals unless public, path from the spec appended to the
   submitted origin only.
2. **Resolve and pin:** resolve every A/AAAA; reject if *any* address is loopback,
   private (RFC 1918), CGNAT (100.64/10), link-local (169.254/16, fe80::/10), unique local
   (fc00::/7), multicast, reserved, unspecified, IPv4-mapped/6to4 forms of those, or cloud
   metadata (169.254.169.254, fd00:ec2::254). Connect to the **pinned** IP with SNI = host
   (defeats DNS rebinding).
3. **Every redirect re-validated** through steps 1–2; cap from the spec.
4. **Browser and Lighthouse egress is forced through an in-worker guard proxy**
   (`--proxy-server=http://127.0.0.1:<port>`) that applies the same rules to every
   sub-resource request; direct egress from Chromium is disabled. WebSockets/WebRTC off.
5. **Container network:** the verifier container has no route to the API/DB private
   network except the DB port it needs (separate DB role); in production, egress
   firewall denies RFC 1918 and metadata ranges as defence in depth.
6. **Resource limits:** per-check timeouts from the spec; whole-run budget (e.g. 180 s);
   max response/page weight (e.g. 15 MB); max artifacts per run; fresh browser context per
   check; no downloads, permissions, notifications, service workers; dialogs dismissed;
   container memory/CPU limits; Chromium sandbox on.
7. No provider-controlled code or AI-generated commands are executed by the worker —
   specs are data, evaluated by fixed code.
8. Dev-only escape hatch: `VERIFIER_ALLOW_PRIVATE_TARGETS=true` permits `localhost`
   fixtures; refused when `APP_ENV=production`; covered by a test.

## Evidence persistence

* Artifacts stored content-addressed (`sha256/<hash>`) in `EVIDENCE_DIR` (Render disk in
  the hosted demo; S3-compatible store is a later option). DB keeps metadata + hash; the
  hash is re-checked on read.
* Screenshots capped in size; Lighthouse JSON stored gzipped; no cookies/storage dumps.
* Results reference artifacts by id, so the approval screen (P06/P07) can show exactly
  what was measured.

## Demo fixtures

`fixtures/landing-page-broken/` (form handler errors, no success text) and
`fixtures/landing-page-fixed/` — static sites deployed as a separate public HTTPS static
site so the worker tests them exactly like customer sites (no SSRF special case).

## Tests

* Guard: table-driven IP classification, fake resolver for rebinding, redirect-to-private,
  IPv6 forms, metadata endpoints, userinfo/port tricks.
* Evaluators and verdict: pure-function tests for every outcome combination.
* Queue: concurrent workers claim each job once; lease expiry → retry; max attempts.
* Integration (CI, real Chromium): broken fixture → FAIL on `contact_form`; fixed fixture
  → PASS; mobile overflow fixture → FAIL.
* Lighthouse: marked `slow`, run against fixtures, asserts a numeric score is recorded
  (not a specific value — scores vary).

## Milestones

| Step | Scope |
|---|---|
| P04a | Models, migration, immutability triggers, queue, verdict evaluator |
| P04b | SSRF guard + HTTP check + evidence store |
| P04c | Playwright worker: mobile viewport + contact form + sink + guard proxy |
| P04d | Lighthouse runner (median of N) |
| P04e | Fixtures deployed; end-to-end broken → fixed run; docs + proof |

## Decisions needed at review

1. Hosted fixtures and evidence storage on Render (needs deploy approval) vs local-only demo.
2. Lighthouse runs per check: 3 (≈ 60–90 s per run, more stable) or 1 (faster, noisier).
3. Contact form against a non-sink destination: `FAIL` or `MANUAL_REVIEW_REQUIRED`.
