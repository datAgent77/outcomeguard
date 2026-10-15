# PayPal integration

OutcomeGuard uses **PayPal Orders v2** and **Payments v2** in the **Sandbox** only.
Production endpoints are refused at configuration time (`PAYPAL_BASE_URL` must be
`https://api-m.sandbox.paypal.com`).

OutcomeGuard is **not an escrow service and never holds funds.** The buyer's funds are
held by PayPal as an *authorization* on the buyer's PayPal account, payable to the
merchant. OutcomeGuard only decides *whether and when* the merchant's server asks PayPal
to capture that authorization.

## Calls

| Step | Endpoint | Code |
|---|---|---|
| OAuth (client credentials, cached, refreshed 60 s early) | `POST /v1/oauth2/token` | `paypal/gateway.py::_access_token` |
| Create order, `intent: AUTHORIZE` | `POST /v2/checkout/orders` | `PaymentService.create_order` |
| Read order (approval check) | `GET /v2/checkout/orders/{id}` | `PaymentService.sync_order` |
| Authorize | `POST /v2/checkout/orders/{id}/authorize` | `PaymentService.authorize_order` |
| Read authorization | `GET /v2/payments/authorizations/{id}` | `refresh_authorization`, pre-capture check |
| Capture | `POST /v2/payments/authorizations/{id}/capture` | `PaymentService.capture_authorization` |
| Read capture | `GET /v2/payments/captures/{id}` | `reconcile_capture` |
| Refund (partial/full) | `POST /v2/payments/captures/{id}/refund` | `PaymentService.refund_capture` |
| Read refund | `GET /v2/payments/refunds/{id}` | `reconcile_refund` |
| Verify webhook | `POST /v1/notifications/verify-webhook-signature` | `services/webhooks.py` |

All mutating calls send `PayPal-Request-Id` and `Prefer: return=representation`.

### Order body

```json
{
  "intent": "AUTHORIZE",
  "purchase_units": [{
    "reference_id": "og_…", "custom_id": "og_…", "invoice_id": "og_…",
    "description": "…",
    "amount": {"currency_code": "USD", "value": "500.00"}
  }],
  "payment_source": {"paypal": {"experience_context": {
    "brand_name": "OutcomeGuard", "shipping_preference": "NO_SHIPPING",
    "user_action": "CONTINUE",
    "return_url": "{PUBLIC_API_URL}/paypal/return",
    "cancel_url": "{PUBLIC_API_URL}/paypal/cancel"
  }}}
}
```

With `payment_source.paypal.experience_context`, PayPal returns the buyer link as
`rel: "payer-action"` (older flows use `rel: "approve"`; both are handled).
`user_action: CONTINUE` because approving only places a hold — nothing is paid yet.

## Trust rules

* **The return redirect is not proof.** `/paypal/return?token=…` only identifies which
  order to look at; the server then `GET`s the order and records `APPROVED` only if
  PayPal says so. Authorization re-reads the order again before calling `authorize`.
* **Amounts come from our database**, never from the browser, and every PayPal response
  (order, authorization, capture, refund) is checked against them. A mismatch blocks
  authorization or leaves the capture/refund `UNKNOWN` with the discrepancy recorded.
* **Payee pinning.** The first authorization pins `merchant_accounts.paypal_merchant_id`
  from `payee.merchant_id`; later authorizations and captures must match it.
* **Intent and reference** of the order returned by PayPal must match ours.

## Idempotency and exactly-once money movement

| Operation | PayPal-Request-Id | DB guard |
|---|---|---|
| Create order | `og-order-{reference}` (stable) | `payment_orders.create_request_id` unique |
| Authorize | `og-auth-{order.id}` (stable) | `payment_authorizations.payment_order_id` unique |
| Capture | `og-cap-{auth.id}-{nonce}` (new nonce only after a definite failure) | `payment_captures.payment_authorization_id` unique + row lock |
| Refund | `og-ref-{client idempotency key}` | `payment_refunds.request_id` unique + capture row lock |

Money-moving calls use **reserve → call → settle**:

1. `SELECT … FOR UPDATE` the parent row, check every invariant, insert a `REQUESTED`
   row carrying the PayPal-Request-Id, commit.
2. Call PayPal outside any DB transaction.
3. Lock again and apply what PayPal actually returned.

Outcome classes:

| PayPal result | Recorded as | Next |
|---|---|---|
| 2xx, status COMPLETED | `completed` | — |
| 2xx, status PENDING | `pending` | webhook / reconcile |
| 4xx (request rejected) | `failed` (no money moved) | a new attempt is allowed |
| timeout / connection error / 5xx | `unknown` | **blocks new attempts**; `reconcile` replays the same PayPal-Request-Id, so PayPal returns the original result instead of capturing twice |
| 4xx `AUTHORIZATION_ALREADY_CAPTURED` | `unknown` | reconcile; never retried blindly |

Refunds in `requested`, `unknown`, `pending` or `completed` all count against the
refundable balance, so an uncertain refund can never be "spent twice".

## Authorization lifetime

PayPal authorizations are valid for **29 days** (`expiration_time` is stored) with a
**3-day honor period** (`honor_period_ends_at` = `create_time` + 3 days). A capture after
`expires_at` is blocked locally before any call. Reauthorization is not implemented
(see KNOWN_LIMITATIONS.md).

## Webhooks

`POST /webhooks/paypal`:

1. Signature verified via PayPal's verify endpoint using the `PAYPAL-*` transmission
   headers and `PAYPAL_WEBHOOK_ID`. Missing headers → `FAILURE` without calling PayPal.
2. Every delivery is stored in `webhook_events`; `paypal_event_id` is unique, so a
   replayed delivery is recorded once and processed once.
3. Verified events **do not trust the payload's state**: the referenced capture /
   authorization / refund is re-read from PayPal and reconciled. Settle functions never
   regress `completed` to an earlier state, so out-of-order events are harmless.

Handled: `PAYMENT.AUTHORIZATION.{CREATED,VOIDED}`,
`PAYMENT.CAPTURE.{COMPLETED,PENDING,DENIED,DECLINED,REFUNDED,REVERSED}`.

## Fees and net amounts

Captures store PayPal's `seller_receivable_breakdown` (`paypal_fee`, `net_amount`) and
refunds store `seller_payable_breakdown` (`paypal_fee` returned, `net_amount` debited from
the merchant). The API's `settlement` block shows these as reported and adds one
**derived** figure, `net_after_refunds_derived` = capture net − merchant debits of
completed refunds (null while any refund is in flight or any input is missing). PayPal's
capture `net_amount` is the at-capture figure and is not updated after refunds. Fees are
never estimated from rates. Live webhook status: see [WEBHOOKS.md](WEBHOOKS.md).

## Rehearsal mode

`PAYMENT_MODE=rehearsal` swaps in `RehearsalPayPalGateway`: no network calls, ids
prefixed `SIM-`, rows written with `environment = simulated`, a yellow "SIMULATED"
approval page instead of PayPal, and `/ready` reports `paypal_environment: "none
(simulated)"`. It must be selected explicitly; the app never falls back to it.

## Running the Sandbox proof (P02 gate)

See [INTEGRATION_PROOF.md](INTEGRATION_PROOF.md#how-to-reproduce).
