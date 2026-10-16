"""Create (or update) the demo organizations and users for a local UI demo.

    DEMO_PASSWORD=... uv run python scripts/seed_demo.py

The password is operator-supplied through the environment (never a default, never
printed). Payment environment follows PAYMENT_MODE (sandbox → Sandbox merchant)."""

from __future__ import annotations

import os
import sys

from outcomeguard.config import PaymentMode, get_settings
from outcomeguard.db import get_sessionmaker
from outcomeguard.models import PaymentEnvironment
from outcomeguard.services.identities import ensure_demo_identities


def main() -> None:
    password = os.environ.get("DEMO_PASSWORD")
    if not password:
        sys.exit("set DEMO_PASSWORD")
    settings = get_settings()
    env = (
        PaymentEnvironment.SIMULATED
        if settings.payment_mode is PaymentMode.REHEARSAL
        else PaymentEnvironment.SANDBOX
    )
    with get_sessionmaker()() as session:
        ids = ensure_demo_identities(session, env, password=password)
        session.commit()
        print(f"demo users ready ({env.value}): {', '.join(sorted(ids.users))}")


if __name__ == "__main__":
    main()
