from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text

from outcomeguard.api.deps import PaymentServiceDep, SessionDep, SettingsDep
from outcomeguard.services.webhooks import handle_webhook

router = APIRouter()

MAX_WEBHOOK_BYTES = 256 * 1024


@router.get("/health", tags=["system"])
def health() -> dict:
    """Liveness: the process is up."""
    return {"status": "ok"}


@router.get("/ready", tags=["system"])
def ready(session: SessionDep, settings: SettingsDep) -> dict:
    """Readiness: database reachable; reports (non-secret) integration configuration."""
    try:
        session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — report any DB failure as not ready
        raise HTTPException(503, f"database unavailable: {type(exc).__name__}") from exc
    return {"status": "ready", "config": settings.public_summary()}


@router.get("/config/public", tags=["system"])
def public_config(settings: SettingsDep) -> dict:
    return settings.public_summary()


@router.post("/webhooks/paypal", tags=["webhooks"])
async def paypal_webhook(request: Request, svc: PaymentServiceDep) -> dict:
    raw = await request.body()
    if len(raw) > MAX_WEBHOOK_BYTES:
        raise HTTPException(413, "payload too large")
    try:
        event = json.loads(raw)
    except ValueError as exc:
        raise HTTPException(400, "invalid JSON") from exc
    if not isinstance(event, dict):
        raise HTTPException(400, "invalid event")
    result = handle_webhook(svc, dict(request.headers), event)
    # 200 for everything we recorded, so PayPal stops retrying a delivery we have stored.
    return {"result": result.status, "detail": result.detail}
