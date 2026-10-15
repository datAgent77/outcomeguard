"""List received PayPal webhook deliveries (ids and outcomes only, never payloads).

uv run python scripts/webhook_report.py [--limit 50]
"""

from __future__ import annotations

import argparse

from sqlalchemy import select

from outcomeguard.db import get_sessionmaker
from outcomeguard.models import WebhookEvent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50)
    args = ap.parse_args()
    session = get_sessionmaker()()
    rows = session.scalars(
        select(WebhookEvent).order_by(WebhookEvent.received_at.desc()).limit(args.limit)
    ).all()
    if not rows:
        print("No webhook deliveries recorded.")
        return 0
    print(f"{'received_at':<26} {'event_type':<32} {'resource_id':<20} {'signature':<9} result")
    for r in rows:
        print(
            f"{r.received_at.isoformat(timespec='seconds'):<26} {r.event_type:<32} "
            f"{(r.resource_id or '-'):<20} {r.verification_status:<9} {r.processing_result or '-'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
