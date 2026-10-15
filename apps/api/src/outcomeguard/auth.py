"""Authentication (password login + server-side sessions) and role-based authorization.

* Passwords: scrypt (n=2^14, r=8, p=1, 16-byte salt), constant-time verification.
* Sessions: 32-byte random bearer tokens; only their SHA-256 is stored, with an expiry and
  revocation. Roles are loaded from the database on every request — never from the token.
* Lockout: 5 consecutive failures lock the account for 15 minutes; responses never reveal
  whether the e-mail exists.
* Human vs service accounts: decisions that must be made by a person (manual reviews,
  capture approvals) refuse service accounts (automation/AI).

This replaces the interim operator-issued demo tokens (P03).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from outcomeguard.models import AuthSession, OrganizationMembership, Role, User, UserKind

SESSION_TTL = timedelta(hours=8)
MAX_FAILED_LOGINS = 5
LOCKOUT = timedelta(minutes=15)
MIN_PASSWORD_LENGTH = 12
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}
# Verifying against this keeps timing similar when the user does not exist.
_DUMMY_HASH: str | None = None


class AuthError(Exception):
    pass


class Forbidden(Exception):
    """Authenticated, but not allowed. `hide` → respond 404 to avoid leaking existence."""

    def __init__(self, message: str, *, hide: bool = False) -> None:
        self.hide = hide
        super().__init__(message)


# ------------------------------------------------------------------ passwords


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return "scrypt${n}${r}${p}${s}${h}".format(
        **_SCRYPT, s=base64.b64encode(salt).decode(), h=base64.b64encode(dk).decode()
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt_b64),
            dklen=32,
            n=int(n),
            r=int(r),
            p=int(p),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk, base64.b64decode(hash_b64))


def _dummy_hash() -> str:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(secrets.token_urlsafe(16))
    return _DUMMY_HASH


# ------------------------------------------------------------------- sessions


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def login(session: Session, email: str, password: str) -> str:
    """Return a new bearer token or raise AuthError (generic message)."""
    now = datetime.now(UTC)
    user = session.scalar(select(User).where(User.email == email.strip().lower()).with_for_update())
    if user is None or not user.password_hash:
        verify_password(password, _dummy_hash())
        session.rollback()
        raise AuthError("invalid credentials")
    if user.locked_until and user.locked_until > now:
        session.rollback()
        raise AuthError("invalid credentials")
    if not verify_password(password, user.password_hash):
        user.failed_logins += 1
        if user.failed_logins >= MAX_FAILED_LOGINS:
            user.locked_until = now + LOCKOUT
            user.failed_logins = 0
        session.commit()
        raise AuthError("invalid credentials")
    user.failed_logins = 0
    user.locked_until = None
    token = secrets.token_urlsafe(32)
    session.add(
        AuthSession(
            user_id=user.id,
            token_sha256=_token_hash(token),
            created_at=now,
            expires_at=now + SESSION_TTL,
        )
    )
    session.commit()
    return token


def logout(session: Session, token: str) -> None:
    row = session.scalar(select(AuthSession).where(AuthSession.token_sha256 == _token_hash(token)))
    if row and row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        session.commit()


def authenticate(session: Session, token: str) -> str:
    """Bearer token → user id, or AuthError."""
    if not token or len(token) > 200:
        raise AuthError("invalid session")
    row = session.scalar(select(AuthSession).where(AuthSession.token_sha256 == _token_hash(token)))
    now = datetime.now(UTC)
    if row is None or row.revoked_at is not None or row.expires_at <= now:
        raise AuthError("invalid session")
    return row.user_id


def set_password(session: Session, user: User, password: str) -> None:
    user.password_hash = hash_password(password)
    user.failed_logins = 0
    user.locked_until = None
    # Changing the password revokes every existing session.
    for s in session.scalars(
        select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
    ):
        s.revoked_at = datetime.now(UTC)


# ------------------------------------------------------------------ principal


@dataclass(frozen=True)
class Principal:
    user_id: str
    email: str
    roles: frozenset[tuple[str, Role]] = field(default_factory=frozenset)  # (org_id, role)
    kind: UserKind = UserKind.HUMAN

    @property
    def label(self) -> str:
        return f"user:{self.email}"

    @property
    def is_human(self) -> bool:
        return self.kind is UserKind.HUMAN

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

    def require_human(self, action: str) -> None:
        if not self.is_human:
            raise Forbidden(f"{action} must be performed by a human account")


def load_principal(session: Session, user_id: str) -> Principal:
    user = session.get(User, user_id)
    if user is None:
        raise AuthError("unknown user")
    rows = session.execute(
        select(OrganizationMembership.organization_id, OrganizationMembership.role).where(
            OrganizationMembership.user_id == user_id
        )
    ).all()
    return Principal(
        user_id=user.id, email=user.email, roles=frozenset((o, r) for o, r in rows), kind=user.kind
    )
