# Evidence verification (P04)

> Status: implemented and tested locally (real Chromium 156, Lighthouse 13.5.0, PostgreSQL).
> Not deployed. Plan and design rationale: [architecture/P04_VERIFICATION_PLAN.md](architecture/P04_VERIFICATION_PLAN.md).

## Flow

```
provider ─POST /agreements/{id}/evidence─▶ evidence_submissions + verification_runs(queued)
                                           + verification_jobs(queued)
worker   ─claim (FOR UPDATE SKIP LOCKED, lease)─▶ runner (subprocess or isolated container)
runner   ─measures through the egress proxy─▶ HTTPX · Chromium · Lighthouse
worker   ─evaluates (domain/verdict.py)─▶ results + artifacts + verdict, one transaction
```

| Piece | File |
|---|---|
| Deterministic outcomes + run verdict (pure) | `src/outcomeguard/domain/verdict.py` |
| SSRF policy (single source of truth) | `src/outcomeguard/verification/netpolicy.py` |
| Egress guard proxy (CONNECT + absolute-URI, IP pinning) | `src/outcomeguard/verification/proxy.py` |
| Runner (measures; no DB, no secrets) | `src/outcomeguard/verification/runner.py` |
| Orchestrator, queue, persistence | `src/outcomeguard/verification/service.py` |
| Worker process | `python -m outcomeguard.verification.worker [--once]` |
| Content-addressed evidence store | `src/outcomeguard/verification/store.py` |
| Form sink + run/artifact API | `src/outcomeguard/api/routes_verification.py` |
| Demo fixtures | `fixtures/` (`landing-page-broken`, `-fixed`, `-exfil`) |
| Verifier container image | `apps/api/Dockerfile.verifier` |
| Container isolation test | `scripts/container_isolation_test.sh` + `docker/verifier-isolation.compose.yml` |

## Checks and rules

| Criterion | Measured by | PASS only if |
|---|---|---|
| `http_availability` | HTTPX via proxy, manual redirect loop, each hop re-validated | final status ∈ expected, redirects ≤ max, HTTPS if required |
| `mobile_viewport` | Chromium (mobile UA, touch, spec viewport) | `scrollWidth ≤ innerWidth + 1`, required selectors visible |
| `contact_form` | Chromium; fills spec fields, clicks submit | a POST reached the configured sink, the sink stored a receipt **in the database**, the success text appeared, and no prohibited request was attempted |
| `lighthouse_performance` | Lighthouse CLI (`chrome-headless-shell`, mobile, simulated throttling) | 1 run; if within ±`LIGHTHOUSE_BAND` (default 5) of the threshold, 2 more runs and the **median of 3** decides |
| `manual_review` | — | never; always `MANUAL_REVIEW_REQUIRED` |

Run verdict: any blocking FAIL → `FAIL`; else any blocking ERROR/NOT_RUN → `INCONCLUSIVE`;
else any blocking MANUAL_REVIEW_REQUIRED → `NEEDS_MANUAL_REVIEW`; else `PASS`. A runner
crash, timeout or lost lease never yields PASS: after `max_attempts` the run is `errored`
with verdict `INCONCLUSIVE`. `is_verified_pass(run_id)` is the only predicate later phases
may use, and it re-checks that every blocking criterion has a PASS result row.

## Contact-form safety

* Approved destination = the sink named by the criterion (`test_endpoint_ref`), hosted by
  OutcomeGuard at `{SINK_BASE_URL}/sinks/{ref}`.
* The browser route handler attaches a short-lived HMAC run token to sink POSTs (the page
  never sees it). Every other non-GET request, and any GET carrying the test values, is
  **aborted inside the browser before transmission**. If the aborted request carried test
  data or was a document navigation → `FAIL FORM_POSTS_TO_UNAPPROVED_DESTINATION`.
* Form in a cross-origin subframe or no sink configured → `MANUAL_REVIEW_REQUIRED`.
* The sink stores field *names* and a SHA-256 of the values, never the values; rejects
  requests without a valid token for a *running* run (403, nothing stored), bodies > 8 KB
  (413), non-form content types (415), empty submissions (422), and more than 5 receipts per
  run (429). It is useless as a general-purpose public endpoint.
* WebSockets from submitted pages are routed to a mock (never connected).

## Network isolation — two independent layers

1. **Policy layer (all environments):** HTTPX, Chromium (incl. sub-resources, `<-loopback>`
   so localhost is proxied too) and Lighthouse all use the egress proxy. The proxy resolves
   once, rejects if *any* address is non-public, and connects to the exact IP it checked.
2. **Network layer (container deployment):** the runner container's only network is the
   internal `sandbox` network shared with the proxy and the worker; it has no route to the
   internet, the database network, or anything else. Verified by
   `scripts/container_isolation_test.sh` (see INTEGRATION_PROOF.md).

Locally (macOS, no containers) only layer 1 applies; that is stated wherever results are
reported.

Chromium's own sandbox is **on** for host runs and **off** in the verifier container
(`VERIFIER_CHROMIUM_SANDBOX=0`, set in `Dockerfile.verifier`): default containers have no
user namespaces, so Chromium cannot start its sandbox there. In that environment the
container (non-root user, internal network whose only peer is the proxy) is the boundary.
Each run records `tool_versions.chromium_sandbox`.

## Credential isolation

* Runner: refuses to start if `PAYPAL_*`, `DATABASE_URL`, `SESSION_SECRET`,
  `OPERATOR_TOKEN` or `SINK_SECRET` is in its environment; the worker launches it with an
  allow-listed environment.
* Worker: `VerifierSettings` declares no PayPal fields (so they are never loaded from
  `.env`), and the worker exits if any `PAYPAL_*` variable is in its environment.

## Persistence and immutability (Postgres triggers)

* `evidence_submissions`, `evidence_artifacts`, `verification_results`,
  `form_sink_receipts`: append-only.
* Results and artifacts can only be inserted while their run is `running`.
* A `completed`/`errored` run cannot change; bindings (submission, agreement version,
  content hash) never change; a `pass` verdict requires `completed`.
* Artifacts are stored content-addressed (`EVIDENCE_DIR/ab/abcd…`); every read re-hashes
  and refuses mismatches (`409` from the API).

## Running locally

```bash
python fixtures/serve.py --port 8020 --sink-url http://127.0.0.1:8010/sinks/default
cd apps/api
PUBLIC_API_URL=http://localhost:8010 uv run uvicorn outcomeguard.main:app --port 8010
uv run python scripts/verification_demo.py        # broken → FAIL, fixed → PASS, transcript
uv run python -m outcomeguard.verification.worker # or: long-running worker
```

`.env` needs `SINK_SECRET`, `SINK_BASE_URL=http://127.0.0.1:8010` and
`VERIFIER_DEV_ALLOW=127.0.0.1:8020,127.0.0.1:8010` (development only).
