# Known limitations

Current as of P02. Each item is either planned (phase noted) or a deliberate scope cut.

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
- **No void flow in the product yet** (gateway method exists; product use planned P06/P08).
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

- **Interim authentication.** Sessions are HMAC-signed bearer tokens issued by the
  operator-gated `/dev/session` endpoint for seeded demo users — there is no password or
  SSO login yet (P06). RBAC itself is enforced from database memberships on every request.
- The P02 payment driver endpoints are operator-token protected and disabled by default.
- **No rate limiting yet** (planned P09).
- **Audit log is tamper-evident, not tamper-proof**: someone with database write access
  can rewrite the whole hash chain.
- **Rehearsal mode keeps simulated PayPal objects in memory**; restarting the API forgets
  them (our database rows remain and will fail to reconcile).
- **No AI or product UI yet** (P05, P07).

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
