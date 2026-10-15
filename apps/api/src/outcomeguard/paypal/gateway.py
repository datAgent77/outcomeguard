"""PayPal REST gateway (Orders v2, Payments v2, Webhooks).

`PayPalGateway` is the seam the services depend on. `HttpPayPalGateway` makes real calls
to the PayPal Sandbox. `RehearsalPayPalGateway` (rehearsal.py) generates clearly labeled
simulated objects for demo rehearsal without credentials.

Error model:
  PayPalAPIError       — PayPal answered with a 4xx: the request was rejected, nothing
                         happened. Safe to record as FAILED.
  PayPalUncertainError — timeout, connection error or 5xx: the operation may or may not
                         have happened. Callers must record UNKNOWN and reconcile
                         (retry with the same PayPal-Request-Id, or read the resource).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Protocol

import httpx

from outcomeguard.logs import redact

log = logging.getLogger(__name__)

Json = dict[str, Any]


class PayPalError(Exception):
    pass


class PayPalNotConfiguredError(PayPalError):
    pass


class PayPalAPIError(PayPalError):
    def __init__(self, status_code: int, body: Json | None, debug_id: str | None) -> None:
        self.status_code = status_code
        self.body = body or {}
        self.debug_id = debug_id
        self.name = self.body.get("name") or self.body.get("error")
        issues = [d.get("issue") for d in self.body.get("details", []) if isinstance(d, dict)]
        self.issues = [i for i in issues if i]
        super().__init__(f"PayPal {status_code} {self.name} {self.issues} debug_id={debug_id}")

    def to_record(self) -> Json:
        return {
            "status_code": self.status_code,
            "name": self.name,
            "issues": self.issues,
            "message": self.body.get("message"),
            "debug_id": self.debug_id,
        }


class PayPalUncertainError(PayPalError):
    def __init__(self, message: str, debug_id: str | None = None) -> None:
        self.debug_id = debug_id
        super().__init__(message)

    def to_record(self) -> Json:
        return {"uncertain": True, "message": str(self), "debug_id": self.debug_id}


class PayPalGateway(Protocol):
    simulated: bool

    def create_order(self, body: Json, *, request_id: str) -> Json: ...
    def get_order(self, order_id: str) -> Json: ...
    def authorize_order(self, order_id: str, *, request_id: str) -> Json: ...
    def get_authorization(self, authorization_id: str) -> Json: ...
    def capture_authorization(
        self, authorization_id: str, body: Json, *, request_id: str
    ) -> Json: ...
    def void_authorization(self, authorization_id: str, *, request_id: str) -> Json | None: ...
    def get_capture(self, capture_id: str) -> Json: ...
    def refund_capture(self, capture_id: str, body: Json, *, request_id: str) -> Json: ...
    def get_refund(self, refund_id: str) -> Json: ...
    def verify_webhook_signature(self, headers: dict[str, str], event: Json) -> str: ...


class HttpPayPalGateway:
    simulated = False

    def __init__(
        self,
        *,
        base_url: str,
        client_id: str | None,
        client_secret: str | None,
        webhook_id: str | None,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._webhook_id = webhook_id
        self._http = httpx.Client(base_url=base_url, timeout=timeout, transport=transport)
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._lock = threading.Lock()

    # -- auth -------------------------------------------------------------------------

    def _access_token(self) -> str:
        if not (self._client_id and self._client_secret):
            raise PayPalNotConfiguredError(
                "PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET are not set (see .env.example)"
            )
        with self._lock:
            if self._token and time.monotonic() < self._token_expires_at:
                return self._token
            try:
                resp = self._http.post(
                    "/v1/oauth2/token",
                    data={"grant_type": "client_credentials"},
                    auth=(self._client_id, self._client_secret),
                    headers={"Accept": "application/json"},
                )
            except httpx.HTTPError as exc:
                raise PayPalUncertainError(f"OAuth request failed: {type(exc).__name__}") from exc
            if resp.status_code != 200:
                raise PayPalAPIError(
                    resp.status_code, _json(resp), resp.headers.get("paypal-debug-id")
                )
            body = resp.json()
            self._token = body["access_token"]
            # Refresh a minute early so a token never expires mid-request.
            self._token_expires_at = time.monotonic() + max(int(body.get("expires_in", 0)) - 60, 0)
            return self._token

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Json | None = None,
        request_id: str | None = None,
        allow_empty: bool = False,
    ) -> Json:
        headers = {
            "Authorization": f"Bearer {self._access_token()}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }
        if request_id:
            headers["PayPal-Request-Id"] = request_id
        try:
            resp = self._http.request(method, path, json=json, headers=headers)
        except httpx.TimeoutException as exc:
            raise PayPalUncertainError(f"{method} {path} timed out") from exc
        except httpx.HTTPError as exc:
            raise PayPalUncertainError(f"{method} {path} failed: {type(exc).__name__}") from exc

        debug_id = resp.headers.get("paypal-debug-id")
        log.info(
            "paypal call",
            extra={
                "data": {
                    "method": method,
                    "path": path,
                    "status": resp.status_code,
                    "debug_id": debug_id,
                    "request_id": request_id,
                }
            },
        )
        if resp.status_code == 401:
            # Token revoked/expired early: drop it so the next call re-authenticates.
            self._token = None
        if resp.status_code >= 500:
            raise PayPalUncertainError(f"{method} {path} -> {resp.status_code}", debug_id)
        if resp.status_code >= 400:
            raise PayPalAPIError(resp.status_code, _json(resp), debug_id)
        if resp.status_code == 204 or not resp.content:
            if allow_empty:
                return {}
            raise PayPalUncertainError(f"{method} {path} returned an empty body", debug_id)
        return resp.json()

    # -- Orders v2 --------------------------------------------------------------------

    def create_order(self, body: Json, *, request_id: str) -> Json:
        return self._request("POST", "/v2/checkout/orders", json=body, request_id=request_id)

    def get_order(self, order_id: str) -> Json:
        return self._request("GET", f"/v2/checkout/orders/{_seg(order_id)}")

    def authorize_order(self, order_id: str, *, request_id: str) -> Json:
        return self._request(
            "POST",
            f"/v2/checkout/orders/{_seg(order_id)}/authorize",
            json={},
            request_id=request_id,
        )

    # -- Payments v2 ------------------------------------------------------------------

    def get_authorization(self, authorization_id: str) -> Json:
        return self._request("GET", f"/v2/payments/authorizations/{_seg(authorization_id)}")

    def capture_authorization(self, authorization_id: str, body: Json, *, request_id: str) -> Json:
        return self._request(
            "POST",
            f"/v2/payments/authorizations/{_seg(authorization_id)}/capture",
            json=body,
            request_id=request_id,
        )

    def void_authorization(self, authorization_id: str, *, request_id: str) -> Json | None:
        return self._request(
            "POST",
            f"/v2/payments/authorizations/{_seg(authorization_id)}/void",
            request_id=request_id,
            allow_empty=True,
        )

    def get_capture(self, capture_id: str) -> Json:
        return self._request("GET", f"/v2/payments/captures/{_seg(capture_id)}")

    def refund_capture(self, capture_id: str, body: Json, *, request_id: str) -> Json:
        return self._request(
            "POST",
            f"/v2/payments/captures/{_seg(capture_id)}/refund",
            json=body,
            request_id=request_id,
        )

    def get_refund(self, refund_id: str) -> Json:
        return self._request("GET", f"/v2/payments/refunds/{_seg(refund_id)}")

    # -- Webhooks ---------------------------------------------------------------------

    def verify_webhook_signature(self, headers: dict[str, str], event: Json) -> str:
        """Ask PayPal to verify a delivery. Returns "SUCCESS" or "FAILURE"."""
        if not self._webhook_id:
            raise PayPalNotConfiguredError("PAYPAL_WEBHOOK_ID is not set")
        h = {k.lower(): v for k, v in headers.items()}
        body = {
            "auth_algo": h.get("paypal-auth-algo"),
            "cert_url": h.get("paypal-cert-url"),
            "transmission_id": h.get("paypal-transmission-id"),
            "transmission_sig": h.get("paypal-transmission-sig"),
            "transmission_time": h.get("paypal-transmission-time"),
            "webhook_id": self._webhook_id,
            "webhook_event": event,
        }
        if not all(body.values()):
            return "FAILURE"
        resp = self._request("POST", "/v1/notifications/verify-webhook-signature", json=body)
        return str(resp.get("verification_status", "FAILURE"))


def _seg(value: str) -> str:
    """PayPal ids are alphanumeric; refuse anything that could alter the URL path."""
    if not value or not value.replace("-", "").replace("_", "").isalnum():
        raise ValueError(f"invalid PayPal resource id {value!r}")
    return value


def _json(resp: httpx.Response) -> Json | None:
    try:
        body = resp.json()
    except ValueError:
        return None
    return redact(body) if isinstance(body, dict) else None


def link(resource: Json, rel: str) -> str | None:
    for item in resource.get("links", []):
        if item.get("rel") == rel:
            return item.get("href")
    return None
