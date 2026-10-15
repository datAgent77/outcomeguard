# Known limitations

Current as of P02. Each item is either planned (phase noted) or a deliberate scope cut.

## Payments

- **Sandbox only.** Production PayPal is refused by configuration.
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

## Platform

- **No authentication / RBAC yet** (planned P06). The payment driver endpoints are
  operator-token protected and disabled by default.
- **No rate limiting yet** (planned P09).
- **Audit log is tamper-evident, not tamper-proof**: someone with database write access
  can rewrite the whole hash chain.
- **Rehearsal mode keeps simulated PayPal objects in memory**; restarting the API forgets
  them (our database rows remain and will fail to reconcile).
- **No AI, verification worker, agreements or product UI yet** (P03–P07).
