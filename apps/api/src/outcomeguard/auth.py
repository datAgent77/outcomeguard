"""Authentication (signed session tokens) and role-based authorization.

Tokens carry only the user id and expiry, signed with HMAC-SHA256 (SESSION_SECRET).
Roles are never read from the token: memberships are loaded from the database on every
request, so revoking a role takes effect immediately.

Interim mechanism (P03): tokens are issued by the operator-gated `/dev/session`
endpoint for seeded demo users. Real login replaces the issuer in P06; the verification
and authorization layer stays.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from outcomeguard.models import OrganizationMembership, Role, User

TOKEN_TTL_SECONDS = 8 * 3600


class AuthError(Exception):
    pass


class Forbidden(Exception):
    """Authenticated, but not allowed. `hide` → respond 404 to avoid leaking existence."""

    def __init__(self, message: str, *, hide: bool = False) -> None:
        self.hide = hide
        super().__init__(message)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_token(secret: str, user_id: str, *, ttl: int = TOKEN_TTL_SECONDS) -> str:
    now = int(time.time())
    payload = _b64(json.dumps({"sub": user_id, "iat": now, "exp": now + ttl}).encode())
    sig = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def verify_token(secret: str, token: str) -> str:
    try:
        payload, sig = token.split(".", 1)
    except ValueError as exc:
        raise AuthError("malformed token") from exc
    expected = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, sig):
        raise AuthError("bad signature")
    try:
        data = json.loads(_unb64(payload))
    except (ValueError, json.JSONDecodeError) as exc:
        raise AuthError("malformed payload") from exc
    if not isinstance(data, dict) or int(data.get("exp", 0)) < time.time():
        raise AuthError("expired")
    sub = data.get("sub")
    if not isinstance(sub, str):
        raise AuthError("no subject")
    return sub


@dataclass(frozen=True)
class Principal:
    user_id: str
    email: str
    roles: frozenset[tuple[str, Role]] = field(default_factory=frozenset)  # (org_id, role)

    @property
    def label(self) -> str:
        return f"user:{self.email}"

    def has(self, org_id: str, *roles: Role) -> bool:
        return any((org_id, r) in self.roles for r in roles)

    def member_of(self, org_id: str) -> bool:
        return any(o == org_id for o, _ in self.roles)

    def require(self, org_id: str, *roles: Role, action: str) -> None:
        if not self.has(org_id, *roles):
            # Members get an honest 403; non-members get 404 (no existence leak).
            raise Forbidden(
                f"{action} requires role {' or '.join(r.value for r in roles)}",
                hide=not self.member_of(org_id),
            )


def load_principal(session: Session, user_id: str) -> Principal:
    user = session.get(User, user_id)
    if user is None:
        raise AuthError("unknown user")
    rows = session.execute(
        select(OrganizationMembership.organization_id, OrganizationMembership.role).where(
            OrganizationMembership.user_id == user_id
        )
    ).all()
    return Principal(user_id=user.id, email=user.email, roles=frozenset((o, r) for o, r in rows))
