"""Rehearsal (simulation) gateway — NOT PayPal.

Used only when PAYMENT_MODE=rehearsal is set explicitly. It reproduces the shape and
state rules of the PayPal objects we use so the demo can be rehearsed without
credentials. Every id it issues starts with `SIM-`, every object carries
`"simulated": true`, and the database rows are written with environment=simulated, so a
simulated result can never be mistaken for a Sandbox transaction.

State lives in process memory: restarting the API forgets simulated PayPal objects
(our own database rows survive and will then fail to reconcile — by design).
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from outcomeguard.paypal.gateway import Json, PayPalAPIError

SIM_MERCHANT_ID = "SIM-MERCHANT"


def _sim_id(prefix: str) -> str:
    return f"SIM-{prefix}-{uuid.uuid4().hex[:12].upper()}"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _err(status: int, name: str, issue: str) -> PayPalAPIError:
    return PayPalAPIError(status, {"name": name, "details": [{"issue": issue}]}, "SIM-DEBUG")


class RehearsalPayPalGateway:
    simulated = True

    def __init__(self, approve_base_url: str) -> None:
        self._approve_base_url = approve_base_url.rstrip("/")
        self._lock = threading.Lock()
        self._orders: dict[str, Json] = {}
        self._auths: dict[str, Json] = {}
        self._captures: dict[str, Json] = {}
        self._refunds: dict[str, Json] = {}
        self._by_request: dict[str, Json] = {}

    def _idem(self, request_id: str, fn: Any) -> Json:
        with self._lock:
            if request_id in self._by_request:
                return self._by_request[request_id]
            result = fn()
            self._by_request[request_id] = result
            return result

    # Orders

    def create_order(self, body: Json, *, request_id: str) -> Json:
        def make() -> Json:
            oid = _sim_id("ORDER")
            order = {
                "id": oid,
                "simulated": True,
                "intent": body["intent"],
                "status": "PAYER_ACTION_REQUIRED",
                "purchase_units": [
                    {**body["purchase_units"][0], "payee": {"merchant_id": SIM_MERCHANT_ID}}
                ],
                "links": [
                    {"rel": "payer-action", "href": f"{self._approve_base_url}/{oid}"},
                ],
            }
            self._orders[oid] = order
            return order

        return self._idem(request_id, make)

    def simulate_buyer_approval(self, order_id: str) -> None:
        with self._lock:
            order = self._orders.get(order_id)
            if not order:
                raise _err(404, "RESOURCE_NOT_FOUND", "INVALID_RESOURCE_ID")
            if order["status"] == "PAYER_ACTION_REQUIRED":
                order["status"] = "APPROVED"
                order["payer"] = {"payer_id": "SIM-PAYER"}

    def get_order(self, order_id: str) -> Json:
        with self._lock:
            order = self._orders.get(order_id)
            if not order:
                raise _err(404, "RESOURCE_NOT_FOUND", "INVALID_RESOURCE_ID")
            return dict(order)

    def authorize_order(self, order_id: str, *, request_id: str) -> Json:
        def make() -> Json:
            order = self._orders.get(order_id)
            if not order:
                raise _err(404, "RESOURCE_NOT_FOUND", "INVALID_RESOURCE_ID")
            if order["status"] == "COMPLETED":
                raise _err(422, "UNPROCESSABLE_ENTITY", "ORDER_ALREADY_AUTHORIZED")
            if order["status"] != "APPROVED":
                raise _err(422, "UNPROCESSABLE_ENTITY", "ORDER_NOT_APPROVED")
            now = datetime.now(UTC)
            auth = {
                "id": _sim_id("AUTH"),
                "simulated": True,
                "status": "CREATED",
                "amount": order["purchase_units"][0]["amount"],
                "create_time": _now(),
                "expiration_time": (now + timedelta(days=29)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "payee": {"merchant_id": SIM_MERCHANT_ID},
            }
            self._auths[auth["id"]] = auth
            order["status"] = "COMPLETED"
            order["purchase_units"][0]["payments"] = {"authorizations": [auth]}
            return dict(order)

        return self._idem(request_id, make)

    # Payments

    def get_authorization(self, authorization_id: str) -> Json:
        with self._lock:
            auth = self._auths.get(authorization_id)
            if not auth:
                raise _err(404, "RESOURCE_NOT_FOUND", "INVALID_RESOURCE_ID")
            return dict(auth)

    def capture_authorization(self, authorization_id: str, body: Json, *, request_id: str) -> Json:
        def make() -> Json:
            auth = self._auths.get(authorization_id)
            if not auth:
                raise _err(404, "RESOURCE_NOT_FOUND", "INVALID_RESOURCE_ID")
            if auth["status"] == "CAPTURED":
                raise _err(422, "UNPROCESSABLE_ENTITY", "AUTHORIZATION_ALREADY_CAPTURED")
            if auth["status"] != "CREATED":
                raise _err(422, "UNPROCESSABLE_ENTITY", "AUTHORIZATION_VOIDED")
            amount = body.get("amount") or auth["amount"]
            cap = {
                "id": _sim_id("CAPTURE"),
                "simulated": True,
                "status": "COMPLETED",
                "amount": amount,
                "final_capture": True,
                "create_time": _now(),
                "payee": {"merchant_id": SIM_MERCHANT_ID},
            }
            cap["invoice_id"] = body.get("invoice_id")
            cap["links"] = [
                {"rel": "up", "href": f"/v2/payments/authorizations/{authorization_id}"}
            ]
            self._captures[cap["id"]] = cap
            auth["status"] = "CAPTURED"
            for order in self._orders.values():  # mirror PayPal: captures appear on the order
                auths = order["purchase_units"][0].get("payments", {}).get("authorizations", [])
                if any(a["id"] == authorization_id for a in auths):
                    order["purchase_units"][0]["payments"].setdefault("captures", []).append(cap)
            return dict(cap)

        return self._idem(request_id, make)

    def void_authorization(self, authorization_id: str, *, request_id: str) -> Json | None:
        def make() -> Json:
            auth = self._auths.get(authorization_id)
            if not auth or auth["status"] != "CREATED":
                raise _err(422, "UNPROCESSABLE_ENTITY", "PREVIOUSLY_CAPTURED")
            auth["status"] = "VOIDED"
            return dict(auth)

        return self._idem(request_id, make)

    def get_capture(self, capture_id: str) -> Json:
        with self._lock:
            cap = self._captures.get(capture_id)
            if not cap:
                raise _err(404, "RESOURCE_NOT_FOUND", "INVALID_RESOURCE_ID")
            return dict(cap)

    def refund_capture(self, capture_id: str, body: Json, *, request_id: str) -> Json:
        def make() -> Json:
            cap = self._captures.get(capture_id)
            if not cap:
                raise _err(404, "RESOURCE_NOT_FOUND", "INVALID_RESOURCE_ID")
            refunded = sum(
                Decimal(r["amount"]["value"])
                for r in self._refunds.values()
                if r["capture_id"] == capture_id
            )
            amount = body.get("amount") or cap["amount"]
            if refunded + Decimal(amount["value"]) > Decimal(cap["amount"]["value"]):
                raise _err(422, "UNPROCESSABLE_ENTITY", "REFUND_AMOUNT_EXCEEDED")
            ref = {
                "id": _sim_id("REFUND"),
                "simulated": True,
                "status": "COMPLETED",
                "amount": amount,
                "capture_id": capture_id,
                "invoice_id": body.get("invoice_id"),
                "create_time": _now(),
            }
            self._refunds[ref["id"]] = ref
            for order in self._orders.values():  # mirror PayPal: refunds appear on the order
                caps = order["purchase_units"][0].get("payments", {}).get("captures", [])
                if any(c["id"] == capture_id for c in caps):
                    order["purchase_units"][0]["payments"].setdefault("refunds", []).append(ref)
            total = refunded + Decimal(amount["value"])
            cap["status"] = (
                "REFUNDED" if total == Decimal(cap["amount"]["value"]) else "PARTIALLY_REFUNDED"
            )
            return dict(ref)

        return self._idem(request_id, make)

    def get_refund(self, refund_id: str) -> Json:
        with self._lock:
            ref = self._refunds.get(refund_id)
            if not ref:
                raise _err(404, "RESOURCE_NOT_FOUND", "INVALID_RESOURCE_ID")
            return dict(ref)

    def verify_webhook_signature(self, headers: dict[str, str], event: Json) -> str:
        # Rehearsal never receives real PayPal webhooks.
        return "FAILURE"
