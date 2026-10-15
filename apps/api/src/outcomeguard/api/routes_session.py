"""Interim session issuance for demo users (operator-gated) and `/me`.

Replaced by real login in P06. Tokens are only issued for users that exist in the
database; roles always come from the database, never from the token.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from outcomeguard.api.deps import OperatorDep, PrincipalDep, SessionDep, SettingsDep
from outcomeguard.auth import issue_token
from outcomeguard.config import PaymentMode
from outcomeguard.models import PaymentEnvironment, User
from outcomeguard.services.identities import ensure_demo_identities

router = APIRouter(tags=["session"])


class SessionIn(BaseModel):
    email: str = Field(max_length=320)


@router.post("/dev/seed")
def seed(session: SessionDep, settings: SettingsDep, _: OperatorDep) -> dict:
    env = (
        PaymentEnvironment.SIMULATED
        if settings.payment_mode is PaymentMode.REHEARSAL
        else PaymentEnvironment.SANDBOX
    )
    ids = ensure_demo_identities(session, env)
    return {
        "buyer_organization_id": ids.buyer_org_id,
        "other_buyer_organization_id": ids.other_buyer_org_id,
        "merchant_account_id": ids.merchant.id,
        "users": ids.users,
    }


@router.post("/dev/session")
def dev_session(
    body: SessionIn, session: SessionDep, settings: SettingsDep, _: OperatorDep
) -> dict:
    if not settings.session_secret:
        raise HTTPException(503, "SESSION_SECRET is not configured")
    user = session.scalar(select(User).where(User.email == body.email))
    if user is None:
        raise HTTPException(404, "unknown user")
    return {"token": issue_token(settings.session_secret.get_secret_value(), user.id)}


@router.get("/me")
def me(principal: PrincipalDep) -> dict:
    return {
        "user_id": principal.user_id,
        "email": principal.email,
        "roles": sorted(
            {"organization_id": o, "role": r.value}.items() for o, r in principal.roles
        ),
    }
