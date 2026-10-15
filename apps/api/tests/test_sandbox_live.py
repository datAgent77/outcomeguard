"""Opt-in tests against the REAL PayPal Sandbox.

    uv run pytest -m sandbox

Skipped unless PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET are configured. Buyer approval
needs a human in a browser, so the full capture/refund path is proven by
scripts/paypal_sandbox_proof.py; this suite covers what can run unattended.
"""

import uuid

import pytest

from outcomeguard.config import Settings
from outcomeguard.paypal.gateway import HttpPayPalGateway, PayPalAPIError, link

pytestmark = pytest.mark.sandbox


@pytest.fixture(scope="module")
def live() -> HttpPayPalGateway:
    s = Settings()
    if not s.paypal_configured:
        pytest.skip("PayPal Sandbox credentials not configured")
    return HttpPayPalGateway(
        base_url=s.paypal_base_url,
        client_id=s.paypal_client_id,
        client_secret=s.paypal_client_secret.get_secret_value(),
        webhook_id=s.paypal_webhook_id,
    )


def test_create_and_read_authorize_order(live):
    rid = f"og-live-{uuid.uuid4().hex[:12]}"
    body = {
        "intent": "AUTHORIZE",
        "purchase_units": [
            {"reference_id": rid, "amount": {"currency_code": "USD", "value": "1.00"}}
        ],
        "payment_source": {
            "paypal": {
                "experience_context": {
                    "return_url": "https://example.com/return",
                    "cancel_url": "https://example.com/cancel",
                    "user_action": "CONTINUE",
                    "shipping_preference": "NO_SHIPPING",
                }
            }
        },
    }
    order = live.create_order(body, request_id=rid)
    assert order["status"] == "PAYER_ACTION_REQUIRED"
    assert link(order, "payer-action")
    # Same PayPal-Request-Id → same order (idempotency).
    again = live.create_order(body, request_id=rid)
    assert again["id"] == order["id"]
    fetched = live.get_order(order["id"])
    assert fetched["intent"] == "AUTHORIZE"
    assert fetched["purchase_units"][0]["amount"]["value"] == "1.00"


def test_unapproved_order_cannot_be_authorized(live):
    rid = f"og-live-{uuid.uuid4().hex[:12]}"
    order = live.create_order(
        {
            "intent": "AUTHORIZE",
            "purchase_units": [{"amount": {"currency_code": "USD", "value": "1.00"}}],
        },
        request_id=rid,
    )
    with pytest.raises(PayPalAPIError) as ei:
        live.authorize_order(order["id"], request_id=f"{rid}-a")
    assert ei.value.status_code == 422
