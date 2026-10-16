"""Structured JSON logging with secret redaction."""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

_SENSITIVE_KEYS = re.compile(
    r"(secret|password|token|authorization|client_id|api[_-]?key|cookie)", re.IGNORECASE
)
_BEARER = re.compile(r"(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)

REDACTED = "[REDACTED]"


def redact(value: Any) -> Any:
    """Recursively redact secrets from log/audit payloads."""
    if isinstance(value, dict):
        return {
            k: (REDACTED if _SENSITIVE_KEYS.search(str(k)) else redact(v)) for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _BEARER.sub(r"\1 " + REDACTED, value)
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        extra = getattr(record, "data", None)
        if extra:
            payload["data"] = redact(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class RedactPayPalQuery(logging.Filter):
    """uvicorn access logs include the full path; PayPal return/cancel URLs carry the order
    token and PayerID in the query string. Keep the path, drop the query."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            path = args[2]
            if path.startswith(("/paypal/", "/rehearsal/")) and "?" in path:
                record.args = (*args[:2], path.split("?", 1)[0] + "?[REDACTED]", *args[3:])
        return True


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # httpx logs full URLs at INFO; keep it quieter.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RedactPayPalQuery) for f in access.filters):
        access.addFilter(RedactPayPalQuery())
