"""Login / logout / me, and the operator-only demo seed.

Sessions are server-side (see auth.py). The interim `/dev/session` token issuer from P03
is removed: every user authenticates with a password.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from outcomeguard.api.deps import OperatorDep, PrincipalDep, SessionDep, SettingsDep
from outcomeguard.auth import MIN_PASSWORD_LENGTH, AuthError, login, logout
from outcomeguard.config import PaymentMode
from outcomeguard.models import PaymentEnvironment
from outcomeguard.services.identities import ensure_demo_identities

router = APIRouter(tags=["session"])


class LoginIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(min_length=1, max_length=200)


class SeedIn(BaseModel):
    # Operator-supplied password for the demo users. Never defaulted, never logged.
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=200)


@router.post("/auth/login")
def auth_login(body: LoginIn, session: SessionDep) -> dict:
    try:
        token = login(session, body.email, body.password)
    except AuthError as exc:
        raise HTTPException(401, "invalid credentials") from exc
    return {"token": token, "token_type": "Bearer", "expires_in": 8 * 3600}


@router.post("/auth/logout", status_code=204)
def auth_logout(
    session: SessionDep, _: PrincipalDep, authorization: Annotated[str | None, Header()] = None
) -> None:
    if authorization and authorization.startswith("Bearer "):
        logout(session, authorization[7:].strip())


@router.get("/me")
def me(principal: PrincipalDep) -> dict:
    return {
        "user_id": principal.user_id,
        "email": principal.email,
        "kind": principal.kind.value,
        "roles": sorted(
            ({"organization_id": o, "role": r.value} for o, r in principal.roles),
            key=lambda x: (x["organization_id"], x["role"]),
        ),
    }


@router.post("/dev/seed")
def seed(body: SeedIn, session: SessionDep, settings: SettingsDep, _: OperatorDep) -> dict:
    env = (
        PaymentEnvironment.SIMULATED
        if settings.payment_mode is PaymentMode.REHEARSAL
        else PaymentEnvironment.SANDBOX
    )
    ids = ensure_demo_identities(session, env, password=body.password)
    return {
        "buyer_organization_id": ids.buyer_org_id,
        "other_buyer_organization_id": ids.other_buyer_org_id,
        "merchant_account_id": ids.merchant.id,
        "users": ids.users,
    }
