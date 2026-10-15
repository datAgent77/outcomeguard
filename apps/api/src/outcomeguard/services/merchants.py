from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from outcomeguard.models import MerchantAccount, Organization, OrgKind, PaymentEnvironment

DEMO_MERCHANT_NAME = "Northwind Studio (demo agency)"


def ensure_demo_merchant(session: Session, environment: PaymentEnvironment) -> MerchantAccount:
    """The single sandbox merchant used by this build (one per environment)."""
    merchant = session.scalar(
        select(MerchantAccount).where(MerchantAccount.environment == environment).limit(1)
    )
    if merchant:
        return merchant
    org = Organization(name=DEMO_MERCHANT_NAME, kind=OrgKind.MERCHANT)
    session.add(org)
    session.flush()
    merchant = MerchantAccount(
        organization_id=org.id, display_name=DEMO_MERCHANT_NAME, environment=environment
    )
    session.add(merchant)
    session.commit()
    return merchant
