from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from outcomeguard.config import Settings, get_settings
from outcomeguard.db import get_session
from outcomeguard.paypal import get_gateway
from outcomeguard.paypal.gateway import PayPalGateway
from outcomeguard.services.payments import PaymentService

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
GatewayDep = Annotated[PayPalGateway, Depends(get_gateway)]


def get_payment_service(
    session: SessionDep, gateway: GatewayDep, settings: SettingsDep
) -> PaymentService:
    return PaymentService(session=session, gateway=gateway, settings=settings)


PaymentServiceDep = Annotated[PaymentService, Depends(get_payment_service)]


def require_operator(
    settings: SettingsDep, x_operator_token: Annotated[str | None, Header()] = None
) -> str:
    """Gate for developer/operator endpoints until real auth + RBAC land in P06."""
    if not settings.enable_sandbox_proof:
        raise HTTPException(404, "not found")
    expected = settings.operator_token.get_secret_value() if settings.operator_token else None
    if not expected or not x_operator_token or not hmac.compare_digest(expected, x_operator_token):
        raise HTTPException(401, "operator token required")
    return "operator"


OperatorDep = Annotated[str, Depends(require_operator)]
