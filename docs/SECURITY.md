# Security and threat model

> Status: P01/P02. "Planned" items are not implemented yet and must not be claimed.

## Assets

1. Buyer funds held under a PayPal authorization.
2. PayPal client secret and OAuth access tokens.
3. Integrity of the decision record (agreement version, evidence, results, approvals).
4. The verification worker's network position (SSRF target).

## Trust boundaries

| Input | Trust | Handling |
|---|---|---|
| Browser requests | untrusted | amounts, merchant, PayPal ids are never accepted from the client; looked up server-side |
| PayPal return redirect (`?token=`) | untrusted | only selects the order; status is read from PayPal |
| PayPal webhook body | untrusted until verified | signature verified with PayPal; state re-read from PayPal API regardless |
| PayPal API responses (TLS) | trusted source, still checked | amount, currency, intent, reference and payee compared to our records |
| Provider evidence (URLs, screenshots, repo, notes) | untrusted | *(planned P04)* SSRF-guarded fetch, sandboxed browser, size/time limits |
| LLM output | untrusted observation | *(planned P05)* schema-validated; never an input to money movement |

## Threats and mitigations

| Threat | Mitigation | Status |
|---|---|---|
| Double capture (double click, retries, concurrent approvers) | row lock + unique capture per authorization + `CAPTURE_IN_PROGRESS`/`UNKNOWN` block + PayPal-Request-Id | implemented, tested (`test_concurrent_captures_move_money_once`) |
| Capture after lost response re-captures | `UNKNOWN` state; reconcile replays the same PayPal-Request-Id | implemented, tested with mocks |
| Refund > captured amount / concurrent refunds | capture row lock; balance includes in-flight and uncertain refunds | implemented, tested |
| Duplicate refund | client idempotency key → stable PayPal-Request-Id; key reuse with different amount rejected | implemented, tested |
| Amount/currency tampering | server-side amounts; response amount checks | implemented, tested |
| Paying the wrong merchant | payee pinned on first authorization; checked on every later auth/capture | implemented, tested |
| Capturing an expired/voided authorization | local expiry check; fresh `GET authorization` before reserving | implemented, tested |
| Forged webhook | PayPal signature verification; unverified events stored but not processed | implemented, tested with mocks; live verification **not yet verified** |
| Webhook replay / reordering | unique `paypal_event_id`; resource re-read; no status regression | implemented, tested |
| Secret leakage | secrets only from env; `SecretStr`; recursive redaction in logs and audit data; secret never sent to the web app | implemented; redaction tested |
| Path injection via PayPal ids | ids validated as alphanumeric before URL use | implemented, tested |
| Accidental production payments | production base URL rejected by config validator | implemented, tested |
| Unauthorized use of dev payment endpoints | disabled by default; constant-time operator token | implemented, tested |
| Undetected audit edits | SHA-256 hash chain, serialized by advisory lock; `verify_chain` | implemented, tested — tamper-*evident* only: a DB admin can rewrite the whole chain |
| Unauthorized agreement edit/approval | DB-loaded roles per request; edit = buyer_admin, approve = buyer_approver/buyer_admin; merchant read-only | implemented, tested |
| Cross-organisation access | non-members get 404 for every read and write | implemented, tested |
| Approving something other than what was reviewed | approval must quote the version's SHA-256 content hash | implemented, tested |
| Rewriting approved agreements or approvals | service checks + Postgres triggers (locked/superseded versions, their criteria, approvals append-only) | implemented, tested with raw SQL |
| Lost updates on drafts | `expected_revision` + row lock | implemented, tested concurrently |
| Session forgery | HMAC-SHA256 tokens, constant-time compare, expiry; roles never in token | implemented, tested |
| Real login / credential handling | password or SSO login, secure cookies | **planned P06** (interim: operator-issued demo tokens) |
| Rate abuse on sensitive endpoints | rate limiting | **planned P09** |
| SSRF from verifier (internal IPs, metadata, DNS rebinding, redirects, IP-encoding tricks) | single policy (`netpolicy.py`) enforced by the egress proxy for HTTPX, Chromium (incl. `<-loopback>`) and Lighthouse; resolve once, reject if any answer is non-public, connect to the checked IP; every redirect is a new proxied request | implemented; unit + real-socket + real-Chromium tests |
| Browser ignoring proxy settings / direct network access | runner container on an internal network whose only peer is the proxy | implemented; **verified only in the container topology** (`scripts/container_isolation_test.sh`), not on a bare host |
| Submitted page JS reaching internal services | proxy policy + (in containers) no route; WebSockets mocked; service workers blocked | implemented; real Chromium test (9 attack vectors, canary received 0 requests) |
| Contact-form data sent to third parties | browser-level abort of every non-sink submission before transmission; FAIL with reason | implemented; e2e test (collector received 0 requests) |
| Form sink abused as a public endpoint | per-run HMAC token added by the verifier, run must be `running`, size/type/count limits, values hashed not stored | implemented, tested |
| Verifier holding PayPal credentials | settings class without PayPal fields; worker and runner refuse PAYPAL_* env; runner env allow-listed | implemented, tested |
| Silent edits of verification results | append-only + run-immutability triggers; PASS requires completed run (DB check) | implemented, tested with raw SQL |
| Tampered evidence files | content-addressed store, hash re-verified on every read | implemented, tested |
| Prompt injection via evidence | evidence passed as data; AI cannot change test results or policy | **planned P05** |

## Operational rules

* `.env` is git-ignored; `.env.example` contains no values.
* Never paste PayPal secrets into chat, issues, or logs.
* Sandbox transaction ids in `docs/integration/runs/` are reviewed before commit; they
  contain no buyer PII beyond sandbox payer ids.
