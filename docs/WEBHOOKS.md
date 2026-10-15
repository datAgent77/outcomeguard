# PayPal webhooks — readiness and live verification procedure

**Status (2026-10-15): implemented and unit-tested with mocks. Live delivery and live
signature verification are NOT verified.** Nothing in this file has been executed
against PayPal yet; it is the procedure to do so.

## What the endpoint does

`POST /webhooks/paypal` (FastAPI, `api/routes_system.py` → `services/webhooks.py`):

1. Rejects bodies > 256 KB and non-JSON.
2. Verifies the signature by calling PayPal `POST /v1/notifications/verify-webhook-signature`
   with the `PAYPAL-AUTH-ALGO`, `PAYPAL-CERT-URL`, `PAYPAL-TRANSMISSION-ID`,
   `PAYPAL-TRANSMISSION-SIG`, `PAYPAL-TRANSMISSION-TIME` headers and `PAYPAL_WEBHOOK_ID`.
   Missing headers → `FAILURE` without calling PayPal. No `PAYPAL_WEBHOOK_ID` → `SKIPPED`.
3. Stores every delivery in `webhook_events` (`paypal_event_id` unique → replay-safe).
4. Only `SUCCESS` events are processed, and processing never trusts the payload: the
   capture / authorization / refund is re-read from PayPal and reconciled; completed
   states never regress.
5. Answers 200 for anything it stored (so PayPal stops retrying), 4xx for garbage.

Subscribed event types (register these in the dashboard):

```
PAYMENT.AUTHORIZATION.CREATED      PAYMENT.AUTHORIZATION.VOIDED
PAYMENT.CAPTURE.COMPLETED          PAYMENT.CAPTURE.PENDING
PAYMENT.CAPTURE.DENIED             PAYMENT.CAPTURE.DECLINED
PAYMENT.CAPTURE.REFUNDED           PAYMENT.CAPTURE.REVERSED
```

## Prerequisite: a public HTTPS URL (needs owner approval)

PayPal only delivers to a publicly reachable HTTPS URL. Two options — **neither has been
done; both need explicit approval** because they create or expose infrastructure:

| Option | What | Trade-off |
|---|---|---|
| A. Render (recommended) | Deploy `render.yaml` (API + Postgres). URL like `https://outcomeguard-api.onrender.com` | Matches the hackathon hosted demo; costs per Render plan |
| B. Temporary tunnel | `cloudflared tunnel --url http://localhost:8010` from a dev machine | Free and quick, but exposes the local API to the internet while running; URL changes each run |

Render settings for the API service (secrets entered in the Render dashboard, never in git):
`PAYMENT_MODE=sandbox`, `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_WEBHOOK_ID`,
`PUBLIC_API_URL=https://<api host>`, `WEB_URL=https://<web host>`, `SESSION_SECRET`
(generated), `ENABLE_SANDBOX_PROOF=false`. The container runs `alembic upgrade head`
before starting; health check path `/ready`.

## Registering the webhook (PayPal Developer Dashboard, Sandbox)

1. *Apps & Credentials* → **Sandbox** → your app → **Webhooks** → *Add Webhook*.
2. URL: `https://<public api host>/webhooks/paypal`.
3. Select the event types listed above → Save.
4. Copy the **Webhook ID** into the API environment as `PAYPAL_WEBHOOK_ID`; restart.
5. `GET /ready` must show `"paypal_webhook_configured": true`.

## Live verification procedure

1. Run a real lifecycle through the public API (the proof script with
   `PUBLIC_API_URL` set to the public host, or the product flow once built).
2. Wait for deliveries (usually seconds; PayPal retries on failure).
3. Inspect what arrived — no payloads, ids only:

   ```bash
   cd apps/api && uv run python scripts/webhook_report.py
   ```

4. **Pass criteria** (all required before marking "Tested against PayPal Sandbox"):
   - at least `PAYMENT.AUTHORIZATION.CREATED`, `PAYMENT.CAPTURE.COMPLETED` and
     `PAYMENT.CAPTURE.REFUNDED` received for the run's ids;
   - `verification_status = SUCCESS` for each (PayPal verified the signature);
   - `processing_result` starts with `processed:` for capture/refund events;
   - redelivery of the same event id is recorded once (check PayPal's *Webhooks Events*
     page "Resend" → our row count unchanged).
5. Record the run in `INTEGRATION_PROOF.md` with event ids and statuses.

**Do not use the dashboard's Webhooks Simulator as proof.** Simulator events are mock
payloads not tied to real transactions; they are not expected to pass signature
verification, and even if stored they would be `unmatched`.
