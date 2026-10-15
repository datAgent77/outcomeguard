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
- **No AI, verification worker or product UI yet** (P04–P07).
