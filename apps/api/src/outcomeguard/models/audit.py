"""Append-only audit log with an application-level hash chain.

Each event stores sha256(prev_hash + canonical event JSON). `verify_chain` detects edits,
deletions or reordering of rows done *outside* the application. This is tamper-evident
within the application, not tamper-proof: someone with database write access can rewrite
the entire chain. See docs/SECURITY.md.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Identity, String, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from outcomeguard.logs import redact
from outcomeguard.models.base import Base, utcnow

GENESIS = "0" * 64
# Arbitrary constant: serialises audit appends across connections.
_AUDIT_LOCK_KEY = 0x0A0D17


class AuditEvent(Base):
    __tablename__ = "audit_events"

    seq: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)


def _digest(prev_hash: str, body: dict[str, Any]) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256((prev_hash + canonical).encode()).hexdigest()


def _body(ev: AuditEvent) -> dict[str, Any]:
    return {
        # Normalise to UTC: Postgres returns timestamptz in the session time zone.
        "occurred_at": ev.occurred_at.astimezone(UTC).isoformat(),
        "actor": ev.actor,
        "action": ev.action,
        "entity_type": ev.entity_type,
        "entity_id": ev.entity_id,
        "data": ev.data,
    }


def record_audit(
    session: Session,
    *,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str,
    data: dict[str, Any] | None = None,
) -> AuditEvent:
    """Append an audit event inside the caller's transaction."""
    session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _AUDIT_LOCK_KEY})
    prev = session.scalar(select(AuditEvent.hash).order_by(AuditEvent.seq.desc()).limit(1))
    ev = AuditEvent(
        occurred_at=utcnow(),
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        # Round-trip through JSON so the stored JSONB matches what we hash.
        data=json.loads(json.dumps(redact(data or {}), default=str)),
        prev_hash=prev or GENESIS,
    )
    ev.hash = _digest(ev.prev_hash, _body(ev))
    session.add(ev)
    session.flush()
    return ev


def verify_chain(session: Session) -> tuple[bool, int | None]:
    """Return (ok, first_bad_seq)."""
    prev = GENESIS
    for ev in session.scalars(select(AuditEvent).order_by(AuditEvent.seq)):
        if ev.prev_hash != prev or ev.hash != _digest(prev, _body(ev)):
            return False, ev.seq
        prev = ev.hash
    return True, None
