"""Users, organizations and merchant accounts.

Authentication and role enforcement arrive in P06; the tables exist from P01 so that
every payment record can be tied to an owning organization from day one.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import ForeignKey, String, UniqueConstraint
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


class PaymentEnvironment(StrEnum):
    SANDBOX = "sandbox"
    SIMULATED = "simulated"  # rehearsal mode — never a real PayPal object


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))


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
