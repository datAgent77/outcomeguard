"""Postgres advisory locks (fixed keys, fixed SQL) shared by subsystems."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

AUDIT_CHAIN = 0x0A0D17
AI_BUDGET = 0x0A1B0D6E


def advisory_xact_lock(session: Session, key: int) -> None:
    """Block until this transaction holds the lock; released at commit/rollback."""
    session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": key})
