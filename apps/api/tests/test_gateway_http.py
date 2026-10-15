"""HttpPayPalGateway against mocked HTTP (respx). Proves request shape and error mapping —
NOT proof of live PayPal integration (see tests/test_sandbox_live.py for that)."""

import httpx
import pytest
import respx

from outcomeguard.config import PAYPAL_SANDBOX_BASE_URL, Settings
from outcomeguard.paypal.gateway import (
    HttpPayPalGateway,
    PayPalAPIError,
    PayPalNotConfiguredError,
    PayPalUncertainError,
)

BASE = PAYPAL_SANDBOX_BASE_URL


def gw(**kw) -> HttpPayPalGateway:
    return HttpPayPalGateway(
        base_url=BASE, client_id="cid", client_secret="csecret", webhook_id="WH-1", **kw
    )


def token_route(mock: respx.MockRouter):
    return mock.post(f"{BASE}/v1/oauth2/token").mock(
        return_value=httpx.Response(200, json={"access_token": "A21AA-test", "expires_in": 32400})
    )


@respx.mock
def test_token_cached_and_headers_sent():
    tok = token_route(respx)
    create = respx.post(f"{BASE}/v2/checkout/orders").mock(
        return_value=httpx.Response(
            201, json={"id": "5O190127TN364715T", "status": "PAYER_ACTION_REQUIRED"}
        )
    )
    g = gw()
    g.create_order({"intent": "AUTHORIZE"}, request_id="og-order-1")
    g.create_order({"intent": "AUTHORIZE"}, request_id="og-order-2")
    assert tok.call_count == 1
    req = create.calls[0].request
    assert req.headers["Authorization"] == "Bearer A21AA-test"
    assert req.headers["PayPal-Request-Id"] == "og-order-1"
    assert req.headers["Prefer"] == "return=representation"
    assert tok.calls[0].request.headers["Authorization"].startswith("Basic ")


@respx.mock
def test_4xx_is_definite_rejection_with_issues():
    token_route(respx)
    respx.post(f"{BASE}/v2/payments/authorizations/AUTH1/capture").mock(
        return_value=httpx.Response(
            422,
            headers={"paypal-debug-id": "dbg123"},
            json={"name": "UNPROCESSABLE_ENTITY", "details": [{"issue": "AUTHORIZATION_VOIDED"}]},
        )
    )
    with pytest.raises(PayPalAPIError) as ei:
        gw().capture_authorization("AUTH1", {}, request_id="r1")
    assert ei.value.issues == ["AUTHORIZATION_VOIDED"]
    assert ei.value.debug_id == "dbg123"


@respx.mock
@pytest.mark.parametrize(
    "effect",
    [httpx.Response(500, json={}), httpx.Response(503), httpx.ReadTimeout("slow")],
)
def test_5xx_and_timeouts_are_uncertain(effect):
    token_route(respx)
    route = respx.post(f"{BASE}/v2/payments/authorizations/AUTH1/capture")
    if isinstance(effect, Exception):
        route.mock(side_effect=effect)
    else:
        route.mock(return_value=effect)
    with pytest.raises(PayPalUncertainError):
        gw().capture_authorization("AUTH1", {}, request_id="r1")


def test_rejects_path_injection():
    with pytest.raises(ValueError):
        gw().get_order("../v1/oauth2/token")


def test_not_configured():
    g = HttpPayPalGateway(base_url=BASE, client_id=None, client_secret=None, webhook_id=None)
    with pytest.raises(PayPalNotConfiguredError):
        g.get_order("ABC")


@respx.mock
def test_webhook_verify_payload():
    token_route(respx)
    route = respx.post(f"{BASE}/v1/notifications/verify-webhook-signature").mock(
        return_value=httpx.Response(200, json={"verification_status": "SUCCESS"})
    )
    headers = {
        "PAYPAL-AUTH-ALGO": "SHA256withRSA",
        "PAYPAL-CERT-URL": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-1",
        "PAYPAL-TRANSMISSION-ID": "t-1",
        "PAYPAL-TRANSMISSION-SIG": "sig",
        "PAYPAL-TRANSMISSION-TIME": "2026-10-15T10:00:00Z",
    }
    assert gw().verify_webhook_signature(headers, {"id": "WH-EVT"}) == "SUCCESS"
    sent = route.calls[0].request.read()
    assert b'"webhook_id":"WH-1"' in sent.replace(b" ", b"")


@respx.mock
def test_webhook_missing_headers_fail_without_calling_paypal():
    assert gw().verify_webhook_signature({}, {"id": "x"}) == "FAILURE"


def test_live_paypal_url_refused():
    with pytest.raises(ValueError):
        Settings(paypal_base_url="https://api-m.paypal.com")


def test_database_url_normalised_to_psycopg():
    s = Settings(database_url="postgres://u:p@host/db")
    assert s.database_url == "postgresql+psycopg://u:p@host/db"
