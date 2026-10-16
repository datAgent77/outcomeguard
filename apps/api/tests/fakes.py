"""Scriptable PayPal fake for unit tests (built on the rehearsal gateway's state rules).

Lets a test inject faults per method:
  fail_before[method] — raise without performing the operation (request rejected/lost)
  fail_after[method]  — perform the operation, then raise (response lost: the dangerous case)
  override[method]    — post-process the response (e.g. tamper the amount)
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any

from outcomeguard.paypal.gateway import Json
from outcomeguard.paypal.rehearsal import RehearsalPayPalGateway


class FakeGateway(RehearsalPayPalGateway):
    def __init__(self) -> None:
        super().__init__(approve_base_url="http://test/rehearsal/approve")
        self.calls: Counter[str] = Counter()
        self.request_ids: dict[str, list[str]] = {}
        self.fail_before: dict[str, Exception] = {}
        self.fail_after: dict[str, Exception] = {}
        self.override: dict[str, Callable[[Json], Json]] = {}
        self.webhook_verification = "SUCCESS"

    def _wrap(self, name: str, fn: Callable[..., Any], *args: Any, **kw: Any) -> Any:
        self.calls[name] += 1
        if "request_id" in kw:
            self.request_ids.setdefault(name, []).append(kw["request_id"])
        if name in self.fail_before:
            raise self.fail_before.pop(name)
        result = fn(*args, **kw)
        if name in self.fail_after:
            raise self.fail_after.pop(name)
        if name in self.override:
            result = self.override[name](dict(result))
        return result

    def create_order(self, body: Json, *, request_id: str) -> Json:
        return self._wrap("create_order", super().create_order, body, request_id=request_id)

    def get_order(self, order_id: str) -> Json:
        return self._wrap("get_order", super().get_order, order_id)

    def authorize_order(self, order_id: str, *, request_id: str) -> Json:
        return self._wrap(
            "authorize_order", super().authorize_order, order_id, request_id=request_id
        )

    def get_authorization(self, authorization_id: str) -> Json:
        return self._wrap("get_authorization", super().get_authorization, authorization_id)

    def capture_authorization(self, authorization_id: str, body: Json, *, request_id: str) -> Json:
        return self._wrap(
            "capture_authorization",
            super().capture_authorization,
            authorization_id,
            body,
            request_id=request_id,
        )

    def get_capture(self, capture_id: str) -> Json:
        return self._wrap("get_capture", super().get_capture, capture_id)

    def void_authorization(self, authorization_id: str, *, request_id: str) -> Json | None:
        return self._wrap(
            "void_authorization",
            super().void_authorization,
            authorization_id,
            request_id=request_id,
        )

    def refund_capture(self, capture_id: str, body: Json, *, request_id: str) -> Json:
        return self._wrap(
            "refund_capture", super().refund_capture, capture_id, body, request_id=request_id
        )

    def get_refund(self, refund_id: str) -> Json:
        return self._wrap("get_refund", super().get_refund, refund_id)

    def verify_webhook_signature(self, headers: dict[str, str], event: Json) -> str:
        self.calls["verify_webhook_signature"] += 1
        return self.webhook_verification

    # test helpers
    def set_capture_status(self, capture_id: str, status: str) -> None:
        self._captures[capture_id]["status"] = status

    def set_auth_status(self, auth_id: str, status: str) -> None:
        self._auths[auth_id]["status"] = status
