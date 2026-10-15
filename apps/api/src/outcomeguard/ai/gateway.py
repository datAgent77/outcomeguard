"""The single path through which the application talks to a model.

Every call: budget check → provider → Pydantic validation → `ai_calls` record. The result
is either a validated object or None with an outcome code; callers must have a
deterministic behaviour for None.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from outcomeguard.ai.provider import AIProvider, AnthropicProvider
from outcomeguard.config import Settings
from outcomeguard.models.ai import AICall

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
    )


@dataclass
class GatewayResult[T]:
    parsed: T | None
    outcome: str
    call: AICall | None


@dataclass
class AIGateway:
    session: Session
    provider: AIProvider | None
    settings: Settings

    def tokens_used_today(self) -> int:
        start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
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

        def record(**kw) -> AICall:
            row = AICall(
                task=task,
                prompt_version=prompt_version,
                prompt_sha256=prompt_hash,
                actor=actor[:200],
                **kw,
            )
            self.session.add(row)
            self.session.flush()
            return row

        if self.tokens_used_today() >= self.settings.ai_daily_token_budget:
            row = record(
                provider=self.provider.name,
                model_requested=self.provider.model,
                outcome="budget_exceeded",
                latency_ms=0,
                error="daily token budget reached; call not made",
            )
            self.session.commit()
            return GatewayResult(None, "budget_exceeded", row)

        res = self.provider.complete(
            system=system, user=user, schema=schema, max_tokens=self.settings.ai_max_output_tokens
        )
        parsed: T | None = None
        outcome = res.outcome
        error = res.error
        if res.outcome == "ok":
            try:
                parsed = schema.model_validate_json(res.text or "")
            except ValidationError as exc:
                outcome = "invalid_output"
                error = f"schema validation failed: {exc.error_count()} error(s)"
        row = record(
            provider=res.provider,
            model_requested=res.model_requested,
            model_used=res.model_used,
            outcome=outcome,
            stop_reason=res.stop_reason,
            latency_ms=res.latency_ms,
            input_tokens=res.input_tokens,
            output_tokens=res.output_tokens,
            cache_read_tokens=res.cache_read_tokens,
            request_id=res.request_id,
            error=(error or "")[:500] or None,
        )
        self.session.commit()
        return GatewayResult(parsed, outcome, row)
