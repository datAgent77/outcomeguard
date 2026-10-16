# Known limitations

Current as of P07. Each item is either planned (phase noted) or a deliberate scope cut.

## Payments

- **Sandbox only.** Production PayPal is refused by configuration.
- **Merchant balance is not read from PayPal.** "Net after refunds" is derived from the
  PayPal-reported capture and refund breakdowns; the Transaction Search / reporting API is
  not used, so the account balance itself is unverified.
- **Single merchant.** One sandbox merchant account receives all payments; no
  multi-party / marketplace payouts (would need PayPal Commerce Platform onboarding).
- **One full capture per authorization.** No partial or multiple captures.
- **No reauthorization.** After the 3-day honor period a capture may still succeed, but
  OutcomeGuard does not reauthorize; after 29 days the authorization is expired and blocked.
- **Void (P08)** is governed and buyer-initiated; once the delivery is verified it needs the provider's agreement through a resolution case.
- **Reversals/chargebacks** (`PAYMENT.CAPTURE.REVERSED`) are recorded via `paypal_status`
  but not modelled as their own state.
- **Refund webhooks for refunds issued outside OutcomeGuard** (e.g. in the PayPal
  dashboard) are recorded as `unmatched`, not imported.

## Agreements

- **Merchant acceptance is not required to lock** a version — only the buyer approves
  (per spec P03). The merchant can read every version. Whether the provider must
  countersign before payment is a P06 policy decision.
- **Amendments after payment authorization**: a newer locked version may change the
  amount. The payment stays bound to the version it was authorized for; P06 policy must
  block capture against a superseded version or require a new authorization.
- **One service category** (landing page) and a fixed template; AI-proposed criteria in P05.
- The contact-form spec references a named test sink; the sink itself is configured in P04.

## Platform

- **Authentication** is password + server-side session (P06); the web UI keeps the session
  token in an HttpOnly cookie via its BFF (P07). No MFA, password reset, self sign-up or SSO.
- The P02 payment driver endpoints are operator-token protected and disabled by default.
- **No rate limiting yet** (planned P09).
- **Audit log is tamper-evident, not tamper-proof**: someone with database write access
  can rewrite the whole hash chain.
- **Rehearsal mode keeps simulated PayPal objects in memory**; restarting the API forgets
  them (our database rows remain and will fail to reconcile).

## Product UI (P07)

- **Policy previews are persisted.** Opening the payment tab (and "Re-evaluate") runs a
  fresh P06 evaluation, which is stored as a `preview` policy decision by design. The
  ledger hides previews unless asked.
- **Dashboard payment eligibility is the last stored evaluation**, shown with its
  timestamp; it is not re-evaluated for the list view.
- **Ledger scale.** The ledger resolves the organization's entity ids and filters the
  audit log with an `IN` list — fine for demo volumes, not for large tenants (TD-14).
- **Provenance is only as good as the record.** The ledger distinguishes human accounts,
  demo accounts, service accounts and the system. It can mark a decision as *scripted* only
  when the persisted rationale carries the `SCRIPTED DEMO DECISION` marker; actions taken
  through demo accounts in the UI are shown as "Demo account", never as a human decision.
- **Sessions do not slide.** The cookie lives as long as the API session (8 h) and is not
  refreshed; afterwards the user signs in again.
- **No in-app PayPal return.** After approving at PayPal the buyer lands on the API's
  confirmation page and returns to the OutcomeGuard tab, which polls the order (bounded,
  6 min) or re-checks on demand.
- **Light theme only**; no dark mode. English only.
- **Accessibility**: automated axe scans pass (no serious/critical issues) and keyboard
  flows are tested; no manual screen-reader audit.

## AI (P05)

- **The AI can only explain what was measured.** P04 captures no browser console or
  JavaScript errors, so for the broken fixture the model correctly reports that the root
  cause (a script error) is not visible in the evidence. Capturing console errors would
  improve explanations.
- **Stated-vs-suggested classification uses keyword checks per criterion kind**; a quote
  that states a requirement in unusual words may be downgraded to a suggestion (the buyer
  can still accept it explicitly).
- **One unsupported kind or invented field rejects the whole draft** (strict schema); the
  buyer can retry or use the template.
- **Evidence explanations take ~12–17 s and ~4.2k in / 2–2.5k out tokens per run** with
  `claude-sonnet-5-5` at effort `high` (≈ $0.03 estimated).
- **Budget enforcement is local and estimated** (TD-9): list prices × reported usage, only
  for calls through this gateway. Use an Anthropic Console spend limit as the real cap.
- **The success-claim check is a phrase list with negation detection**; unusual phrasing
  can slip past or be flagged. Displayed outcomes always come from stored results, so a
  missed phrase cannot change a verdict.
- **Two real-model tests only**; no broad evaluation set yet (TECH_DEBT TD-6).

## Verification (P04)

- **Network-layer isolation exists only in the container topology.** On a bare host
  (e.g. local macOS development) the egress proxy is the only layer; a Chromium bug that
  ignored proxy settings would not be contained there.
- **Local fixtures are served over HTTP**, so the demo agreement sets
  `require_https: false` for the availability check. Hosted fixtures will use HTTPS.
- **Contact forms must post to the OutcomeGuard sink** to be verifiable. A real site that
  posts to its own backend is FAIL (prohibited destination) unless it offers a
  verification mode; forms in cross-origin iframes are manual review.
- **Lighthouse scores on local fixtures are high (100)** because the pages are tiny and
  served from localhost; simulated throttling is used, but scores on real hosted sites
  will differ.
- **Single worker process locally**; concurrency of multiple workers is tested at the queue
  level, not under production load. No heartbeat: a job whose run exceeds the lease
  (default 10 min) can be re-claimed; the first worker then fails with LEASE_LOST.
- **Evidence store is a local directory**; hosted storage (Render disk / S3) is P10.
- The verifier worker's DB role is not yet a restricted role (same DB user as the API).
- **Chromium's own sandbox is off in containers and in CI** (no unprivileged user
  namespaces there); it is on for host runs. In containers the network/container boundary
  is the isolation; in CI the runner VM is ephemeral.
- **The full `docker-compose.yml` stack has not been run end-to-end in containers**; the
  isolation topology (`docker/verifier-isolation.compose.yml`) has.

## Resolution and refunds (P08)

- **Internal cases are not PayPal disputes.** Disputes and chargebacks opened at PayPal are
  not imported, prevented or synchronised; refund availability is not guaranteed.
- **Both parties must approve every refund.** There is no administrator override.
- **Lost refunds older than 6 h** are not replayed automatically; they become
  `reconciliation_required` and wait for an operator (no tooling yet, TD-18).
- **No notifications** (TD-19). **No AI** in resolution.
- Voids are buyer-initiated; once P06 reports the delivery verified, a two-sided
  `cancel_authorization` proposal is required.
