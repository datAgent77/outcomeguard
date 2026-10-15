"""Demo organizations and users (idempotent). Used by the demo seed and tests."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from outcomeguard.models import (
    MerchantAccount,
    Organization,
    OrganizationMembership,
    OrgKind,
    PaymentEnvironment,
    Role,
    User,
)
from outcomeguard.services.merchants import ensure_demo_merchant

BUYER_ORG = "Acme Outdoor Co. (demo buyer)"
OTHER_BUYER_ORG = "Globex Corp. (second demo buyer)"


@dataclass
class DemoIdentities:
    buyer_org_id: str
    other_buyer_org_id: str
    merchant: MerchantAccount
    users: dict[str, str]  # email -> user id


def _org(session: Session, name: str, kind: OrgKind) -> Organization:
    org = session.scalar(select(Organization).where(Organization.name == name))
    if org is None:
        org = Organization(name=name, kind=kind)
        session.add(org)
        session.flush()
    return org


def _user(session: Session, email: str, name: str, memberships: list[tuple[str, Role]]) -> User:
    user = session.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, display_name=name)
        session.add(user)
        session.flush()
    for org_id, role in memberships:
        exists = session.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.user_id == user.id,
                OrganizationMembership.organization_id == org_id,
                OrganizationMembership.role == role,
            )
        )
        if not exists:
            session.add(OrganizationMembership(organization_id=org_id, user_id=user.id, role=role))
    session.flush()
    return user


def ensure_demo_identities(session: Session, environment: PaymentEnvironment) -> DemoIdentities:
    buyer = _org(session, BUYER_ORG, OrgKind.BUYER)
    other = _org(session, OTHER_BUYER_ORG, OrgKind.BUYER)
    merchant = ensure_demo_merchant(session, environment)
    m_org = merchant.organization_id
    users = {
        u.email: u.id
        for u in [
            _user(
                session, "alice@acme.demo", "Alice (buyer admin)", [(buyer.id, Role.BUYER_ADMIN)]
            ),
            _user(
                session, "bob@acme.demo", "Bob (buyer approver)", [(buyer.id, Role.BUYER_APPROVER)]
            ),
            _user(
                session,
                "maya@northwind.demo",
                "Maya (agency owner)",
                [(m_org, Role.MERCHANT_ADMIN), (m_org, Role.PROVIDER)],
            ),
            _user(session, "eve@globex.demo", "Eve (other buyer)", [(other.id, Role.BUYER_ADMIN)]),
        ]
    }
    session.commit()
    return DemoIdentities(
        buyer_org_id=buyer.id, other_buyer_org_id=other.id, merchant=merchant, users=users
    )
