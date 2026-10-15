"""Users, organizations and merchant accounts.

Authentication and role enforcement arrive in P06; the tables exist from P01 so that
every payment record can be tied to an owning organization from day one.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from outcomeguard.models.base import Base, IdMixin, TimestampMixin, enum_col


class OrgKind(StrEnum):
    BUYER = "buyer"
    MERCHANT = "merchant"


class Role(StrEnum):
    BUYER_ADMIN = "buyer_admin"
    BUYER_APPROVER = "buyer_approver"
    MERCHANT_ADMIN = "merchant_admin"
    MERCHANT_APPROVER = "merchant_approver"
    PROVIDER = "provider"  # the freelancer/agency submitting evidence
    BUYER_REVIEWER = "buyer_reviewer"  # decides manual-review criteria (P06)


class UserKind(StrEnum):
    HUMAN = "human"
    SERVICE = "service"  # automation / AI integrations — never a human approver


class PaymentEnvironment(StrEnum):
    SANDBOX = "sandbox"
    SIMULATED = "simulated"  # rehearsal mode — never a real PayPal object


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[UserKind] = mapped_column(
        enum_col(UserKind, "user_kind"), default=UserKind.HUMAN, server_default="human"
    )
    # "scrypt$n$r$p$salt_b64$hash_b64"; NULL → password login disabled.
    password_hash: Mapped[str | None] = mapped_column(String(300))
    failed_logins: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Organization(IdMixin, TimestampMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[OrgKind] = mapped_column(enum_col(OrgKind, "org_kind"))


class OrganizationMembership(IdMixin, TimestampMixin, Base):
    __tablename__ = "organization_memberships"
    __table_args__ = (UniqueConstraint("organization_id", "user_id", "role"),)

    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    role: Mapped[Role] = mapped_column(enum_col(Role, "membership_role"))


class MerchantAccount(IdMixin, TimestampMixin, Base):
    """The PayPal account that receives funds. One sandbox merchant in this build.

    `paypal_merchant_id` is learned from PayPal responses (payee.merchant_id) and then
    pinned: every later authorization/capture must report the same payee.
    """

    __tablename__ = "merchant_accounts"

    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    display_name: Mapped[str] = mapped_column(String(200))
    environment: Mapped[PaymentEnvironment] = mapped_column(
        enum_col(PaymentEnvironment, "payment_environment")
    )
    paypal_merchant_id: Mapped[str | None] = mapped_column(String(64), unique=True)


class AuthSession(IdMixin, Base):
    """Server-side session. Only the SHA-256 of the bearer token is stored."""

    __tablename__ = "auth_sessions"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
