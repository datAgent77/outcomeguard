from __future__ import annotations

from functools import lru_cache

from outcomeguard.config import PaymentMode, get_settings
from outcomeguard.paypal.gateway import HttpPayPalGateway, PayPalGateway
from outcomeguard.paypal.rehearsal import RehearsalPayPalGateway


@lru_cache
def get_gateway() -> PayPalGateway:
    s = get_settings()
    if s.payment_mode is PaymentMode.REHEARSAL:
        return RehearsalPayPalGateway(approve_base_url=f"{s.public_api_url}/rehearsal/approve")
    return HttpPayPalGateway(
        base_url=s.paypal_base_url,
        client_id=s.paypal_client_id,
        client_secret=s.paypal_client_secret.get_secret_value() if s.paypal_client_secret else None,
        webhook_id=s.paypal_webhook_id,
        timeout=s.paypal_timeout_seconds,
    )
