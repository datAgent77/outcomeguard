"""P02 PayPal Sandbox feasibility gate — real calls, real buyer approval.

    OAuth → Create Order (intent AUTHORIZE) → buyer approves in browser → Authorize
          → Capture → Partial Refund → re-read Capture from PayPal

Runs through the same PaymentService the API uses, persists every step to DATABASE_URL,
and writes a sanitized transcript to docs/integration/runs/. Requires PAYMENT_MODE=sandbox
and PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET in .env (never pass secrets on the CLI).

    uv run python scripts/paypal_sandbox_proof.py [--amount 500.00] [--refund 50.00]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
import webbrowser
from datetime import UTC, datetime
from pathlib import Path

from outcomeguard.config import PaymentMode, get_settings
from outcomeguard.db import get_sessionmaker
from outcomeguard.models import PaymentEnvironment
from outcomeguard.models.audit import verify_chain
from outcomeguard.money import Money
from outcomeguard.paypal import get_gateway
from outcomeguard.services.merchants import ensure_demo_merchant
from outcomeguard.services.payments import PaymentService

REPO = Path(__file__).resolve().parents[3]


def step(n: int, title: str, **facts: object) -> dict:
    print(f"\n[{n}] {title}")
    for k, v in facts.items():
        print(f"    {k}: {v}")
    return {"step": n, "title": title, **{k: str(v) for k, v in facts.items()}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--amount", default="500.00")
    ap.add_argument("--refund", default="50.00")
    ap.add_argument("--currency", default="USD")
    ap.add_argument("--timeout", type=int, default=600, help="seconds to wait for approval")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    settings = get_settings()
    if settings.payment_mode is not PaymentMode.SANDBOX:
        print("PAYMENT_MODE must be 'sandbox' for the feasibility gate.", file=sys.stderr)
        return 2
    if not settings.paypal_configured:
        print("Set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET in .env first.", file=sys.stderr)
        return 2

    session = get_sessionmaker()()
    svc = PaymentService(session=session, gateway=get_gateway(), settings=settings)
    transcript: list[dict] = []
    started = datetime.now(UTC)

    merchant = ensure_demo_merchant(session, PaymentEnvironment.SANDBOX)
    amount = Money.from_decimal_str(args.amount, args.currency)
    order = svc.create_order(
        merchant=merchant,
        amount=amount,
        description="OutcomeGuard P02 sandbox proof",
        actor="sandbox-proof",
    )
    transcript.append(
        step(
            1,
            "OAuth + Create Order (intent=AUTHORIZE)",
            paypal_order_id=order.paypal_order_id,
            paypal_status=order.paypal_status,
            amount=amount,
        )
    )

    print("\n    Approve the payment as the SANDBOX PERSONAL (buyer) account:")
    print(f"    {order.approval_url}")
    if not args.no_browser and order.approval_url:
        webbrowser.open(order.approval_url)

    deadline = time.monotonic() + args.timeout
    while True:
        order = svc.sync_order(order, actor="sandbox-proof")
        if order.paypal_status == "APPROVED":
            break
        if time.monotonic() > deadline:
            print("Timed out waiting for buyer approval.", file=sys.stderr)
            return 1
        time.sleep(3)
    transcript.append(
        step(
            2,
            "Buyer approval confirmed by GET order (not by redirect)",
            paypal_status=order.paypal_status,
            payer_id=order.payer_id,
        )
    )

    auth = svc.authorize_order(order.id, actor="sandbox-proof")
    transcript.append(
        step(
            3,
            "Authorize order",
            paypal_authorization_id=auth.paypal_authorization_id,
            paypal_status=auth.paypal_status,
            payee=auth.payee_merchant_id,
            expires_at=auth.expires_at,
        )
    )

    cap = svc.capture_authorization(auth.id, actor="sandbox-proof")
    transcript.append(
        step(
            4,
            "Capture authorization",
            paypal_capture_id=cap.paypal_capture_id,
            paypal_status=cap.paypal_status,
            status=cap.status.value,
            request_id=cap.request_id,
        )
    )
    if cap.status.value != "completed":
        print(f"Capture is {cap.status.value}; refund needs a COMPLETED capture.", file=sys.stderr)
        return 1

    refund = svc.refund_capture(
        cap.id,
        amount=Money.from_decimal_str(args.refund, args.currency),
        idempotency_key=f"proof-{uuid.uuid4().hex[:12]}",
        actor="sandbox-proof",
        note="OutcomeGuard partial refund (sandbox proof)",
    )
    transcript.append(
        step(
            5,
            "Partial refund",
            paypal_refund_id=refund.paypal_refund_id,
            paypal_status=refund.paypal_status,
            amount=Money(refund.amount_minor, refund.currency),
        )
    )

    remote = svc.gateway.get_capture(cap.paypal_capture_id or "")
    transcript.append(
        step(
            6,
            "Re-read capture from PayPal",
            paypal_status=remote.get("status"),
            refundable_balance=svc.refundable_balance(cap),
        )
    )

    ok, bad = verify_chain(session)
    transcript.append(step(7, "Audit chain verification", intact=ok, first_bad_seq=bad))

    out_dir = REPO / "docs" / "integration" / "runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"sandbox-proof-{started.strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(
        json.dumps(
            {
                "kind": "paypal-sandbox-proof",
                "environment": "sandbox",
                "started_at": started.isoformat(),
                "finished_at": datetime.now(UTC).isoformat(),
                "steps": transcript,
            },
            indent=2,
        )
    )
    print(f"\nTranscript (no secrets): {out.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
