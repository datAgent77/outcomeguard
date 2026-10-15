from __future__ import annotations

import os

TEST_DB = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://localhost/outcomeguard_test")
os.environ["DATABASE_URL"] = TEST_DB
os.environ.setdefault("PAYMENT_MODE", "rehearsal")
os.environ["ENABLE_SANDBOX_PROOF"] = "true"
os.environ["OPERATOR_TOKEN"] = "test-operator-token"
os.environ["SESSION_SECRET"] = "test-session-secret"

from collections.abc import Iterator  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from outcomeguard.config import PaymentMode, Settings  # noqa: E402
from outcomeguard.models import Base, MerchantAccount, PaymentEnvironment  # noqa: E402
from outcomeguard.money import Money  # noqa: E402
from outcomeguard.services.merchants import ensure_demo_merchant  # noqa: E402
from outcomeguard.services.payments import PaymentService  # noqa: E402
from tests.fakes import FakeGateway  # noqa: E402

API_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(TEST_DB)
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", TEST_DB)
    cfg.attributes["skip_logging"] = True
    command.upgrade(cfg, "head")
    yield eng
    eng.dispose()


@pytest.fixture
def make_session(engine) -> Iterator[sessionmaker[Session]]:
    tables = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def session(make_session) -> Iterator[Session]:
    s = make_session()
    yield s
    s.close()


@pytest.fixture
def settings() -> Settings:
    return Settings(database_url=TEST_DB, payment_mode=PaymentMode.REHEARSAL)


@pytest.fixture
def gateway() -> FakeGateway:
    return FakeGateway()


@pytest.fixture
def svc(session, gateway, settings) -> PaymentService:
    return PaymentService(session=session, gateway=gateway, settings=settings)


@pytest.fixture
def merchant(session) -> MerchantAccount:
    return ensure_demo_merchant(session, PaymentEnvironment.SIMULATED)


USD500 = Money(50000, "USD")


@pytest.fixture
def approved_order(svc, merchant, gateway):
    order = svc.create_order(
        merchant=merchant, amount=USD500, description="Landing page", actor="t"
    )
    gateway.simulate_buyer_approval(order.paypal_order_id)
    return order


@pytest.fixture
def authorization(svc, approved_order):
    return svc.authorize_order(approved_order.id, actor="buyer")


@pytest.fixture
def capture(svc, authorization):
    return svc.capture_authorization(authorization.id, actor="approver")


# ---------------------------------------------------------------- P03 fixtures


@pytest.fixture
def ids(session):
    from outcomeguard.services.identities import ensure_demo_identities

    return ensure_demo_identities(session, PaymentEnvironment.SIMULATED)


@pytest.fixture
def who(session, ids):
    """who("alice") -> Principal loaded from the database."""
    from outcomeguard.auth import load_principal

    emails = {e.split("@")[0]: e for e in ids.users}
    return lambda name: load_principal(session, ids.users[emails[name]])
