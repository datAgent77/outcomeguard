"""The single path through which the application talks to a model.

Every call:
  1. price lookup (unknown price → fail closed, no call);
  2. budget reservation: under a Postgres advisory lock, today's settled + outstanding
     reservations + this request's worst-case cost must fit the daily USD limit (and the
     token limit) — otherwise fail closed, no call;
  3. provider call;
  4. Pydantic validation;
  5. settlement of the reservation from reported usage (no usage → the full reservation
     is kept), and an append-only `ai_calls` record.

All costs are LOCAL ESTIMATES from the published price list, not Anthropic billing.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from outcomeguard.ai.pricing import conservative_input_tokens, max_cost, price_for, usage_cost
from outcomeguard.ai.provider import AIProvider, AnthropicProvider
from outcomeguard.config import Settings
from outcomeguard.locks import AI_BUDGET, advisory_xact_lock
from outcomeguard.models.ai import AICall, AISpendReservation, ReservationStatus

T = TypeVar("T", bound=BaseModel)


def build_provider(settings: Settings) -> AIProvider | None:
    if settings.ai_provider != "anthropic" or not settings.anthropic_api_key:
        return None
    return AnthropicProvider(
        api_key=settings.anthropic_api_key.get_secret_value(),
        model=settings.ai_model,
        effort=settings.ai_effort,
        timeout_s=settings.ai_timeout_seconds,
        max_retries=settings.ai_max_retries,
        server_fallback=settings.ai_server_fallback,
    )


@dataclass
class GatewayResult[T]:
    parsed: T | None
    outcome: str
    call: AICall | None


def _today() -> datetime:
    return datetime.now(UTC)


@dataclass
class AIGateway:
    session: Session
    provider: AIProvider | None
    settings: Settings

    # ------------------------------------------------------------------ budget

    def spent_today_usd(self) -> Decimal:
        day = _today().date()
        total = self.session.scalar(
            select(
                func.coalesce(
                    func.sum(
                        func.coalesce(
                            AISpendReservation.settled_usd, AISpendReservation.reserved_usd
                        )
                    ),
                    0,
                )
            ).where(AISpendReservation.day == day)
        )
        return Decimal(total or 0)

    def tokens_used_today(self) -> int:
        start = _today().replace(hour=0, minute=0, second=0, microsecond=0)
        total = self.session.scalar(
            select(
                func.coalesce(
                    func.sum(
                        func.coalesce(AICall.input_tokens, 0)
                        + func.coalesce(AICall.output_tokens, 0)
                    ),
                    0,
                )
            ).where(AICall.created_at >= start)
        )
        return int(total or 0)

    def reserve(self, *, task: str, model: str, amount: Decimal) -> AISpendReservation | None:
        """Atomically reserve `amount` for today, or return None (fail closed)."""
        advisory_xact_lock(self.session, AI_BUDGET)
        if self.tokens_used_today() >= self.settings.ai_daily_token_budget:
            self.session.rollback()
            return None
        if self.spent_today_usd() + amount > self.settings.ai_daily_budget_usd:
            self.session.rollback()
            return None
        r = AISpendReservation(
            day=_today().date(),
            model=model,
            task=task,
            reserved_usd=amount,
            status=ReservationStatus.RESERVED,
        )
        self.session.add(r)
        self.session.commit()  # releases the advisory lock with the reservation visible
        return r

    # -------------------------------------------------------------------- call

    def call(
        self,
        *,
        task: str,
        prompt_version: str,
        system: str,
        user: str,
        schema: type[T],
        actor: str,
    ) -> GatewayResult[T]:
        prompt_hash = hashlib.sha256(f"{prompt_version}\n{system}\n{user}".encode()).hexdigest()
        if self.provider is None:
            return GatewayResult(None, "not_configured", None)
        provider = self.provider
        model = provider.model

        def record(**kw) -> AICall:
            row = AICall(
                task=task,
                prompt_version=prompt_version,
                prompt_sha256=prompt_hash,
                actor=actor[:200],
                provider=provider.name,
                model_requested=model,
                **kw,
            )
            self.session.add(row)
            self.session.flush()
            return row

        price = price_for(model)
        if price is None:
            row = record(
                outcome="pricing_unavailable",
                latency_ms=0,
                error=f"no price configured for {model}; call not made",
            )
            self.session.commit()
            return GatewayResult(None, "pricing_unavailable", row)

        schema_text = json.dumps(schema.model_json_schema())
        est_in = conservative_input_tokens(system, user, schema_text)
        attempts = 1 + max(0, self.settings.ai_max_retries)
        worst = max_cost(price, est_in, self.settings.ai_max_output_tokens, attempts)
        reservation = self.reserve(task=task, model=model, amount=worst)
        if reservation is None:
            row = record(
                outcome="budget_exceeded",
                latency_ms=0,
                reserved_cost_usd=worst,
                pricing=price.as_dict(),
                error="daily budget cannot cover the worst-case cost; call not made",
            )
            self.session.commit()
            return GatewayResult(None, "budget_exceeded", row)

        res = self.provider.complete(
            system=system, user=user, schema=schema, max_tokens=self.settings.ai_max_output_tokens
        )

        if res.input_tokens is not None and res.output_tokens is not None:
            estimated = usage_cost(
                price,
                input_tokens=res.input_tokens,
                output_tokens=res.output_tokens,
                cache_read_tokens=res.cache_read_tokens or 0,
                cache_write_tokens=res.cache_write_tokens or 0,
            )
            basis = "usage"
        else:
            estimated, basis = worst, "reserve_kept"  # usage unknown → assume worst case

        parsed: T | None = None
        outcome, error = res.outcome, res.error
        if res.outcome == "ok":
            try:
                parsed = schema.model_validate_json(res.text or "")
            except ValidationError as exc:
                outcome = "invalid_output"
                error = f"schema validation failed: {exc.error_count()} error(s)"

        r = self.session.get(
            AISpendReservation, reservation.id, with_for_update=True, populate_existing=True
        )
        assert r is not None
        r.status = ReservationStatus.SETTLED
        r.settled_usd = estimated
        r.settlement_basis = basis
        r.settled_at = _today()
        row = record(
            model_used=res.model_used,
            outcome=outcome,
            stop_reason=res.stop_reason,
            latency_ms=res.latency_ms,
            input_tokens=res.input_tokens,
            output_tokens=res.output_tokens,
            cache_read_tokens=res.cache_read_tokens,
            cache_write_tokens=res.cache_write_tokens,
            request_id=res.request_id,
            reserved_cost_usd=worst,
            estimated_cost_usd=estimated,
            pricing=price.as_dict(),
            reservation_id=reservation.id,
            error=(error or "")[:500] or None,
        )
        self.session.commit()
        return GatewayResult(parsed, outcome, row)
