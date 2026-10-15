from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from outcomeguard.auth import AuthError, Principal, load_principal, verify_token
from outcomeguard.config import Settings, get_settings
from outcomeguard.db import get_session
from outcomeguard.paypal import get_gateway
from outcomeguard.paypal.gateway import PayPalGateway
from outcomeguard.services.agreements import AgreementService
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


def get_principal(
    session: SessionDep,
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    if not settings.session_secret:
        raise HTTPException(503, "SESSION_SECRET is not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthError("missing bearer token")
    user_id = verify_token(settings.session_secret.get_secret_value(), authorization[7:])
    return load_principal(session, user_id)


PrincipalDep = Annotated[Principal, Depends(get_principal)]


def get_agreement_service(session: SessionDep) -> AgreementService:
    return AgreementService(session=session)


AgreementServiceDep = Annotated[AgreementService, Depends(get_agreement_service)]
