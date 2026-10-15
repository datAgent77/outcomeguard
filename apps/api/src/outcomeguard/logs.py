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


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # httpx logs full URLs at INFO; keep it quieter.
    logging.getLogger("httpx").setLevel(logging.WARNING)
