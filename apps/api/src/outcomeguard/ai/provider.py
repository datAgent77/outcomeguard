"""AI provider abstraction.

`AIProvider.complete()` returns the model's raw JSON text plus call metadata; it never
raises for model/provider problems — every failure becomes an `outcome` the caller must
handle. Parsing and validation happen in the caller with Pydantic, identically for the
real provider and for test doubles, so malformed output is handled in one place.

Providers:
  AnthropicProvider — real Claude API (Anthropic Python SDK).
  ScriptedProvider  — test double returning scripted text/outcomes. Never used outside tests.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel


@dataclass
class AIResult:
    outcome: str  # ok | refusal | timeout | unavailable | rate_limited | not_configured | error
    text: str | None
    provider: str
    model_requested: str
    model_used: str | None = None
    latency_ms: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    request_id: str | None = None
    stop_reason: str | None = None
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class AIProvider(Protocol):
    name: str
    model: str

    def complete(
        self, *, system: str, user: str, schema: type[BaseModel], max_tokens: int
    ) -> AIResult: ...


class AnthropicProvider:
    """Claude via the official SDK, structured output constrained to the Pydantic schema.

    * Server-side fallback (`fallbacks: "default"`) is OFF unless `server_fallback=True`, so
      the model that answers is the model requested; `model_used` records the actual ID
      returned by the API either way.
    * Timeouts and retries are explicit; the API key comes from settings, never logged.
    """

    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        effort: str,
        timeout_s: float,
        max_retries: int,
        server_fallback: bool = False,
    ) -> None:
        import anthropic

        self._anthropic = anthropic
        self.model = model
        self.effort = effort
        self.server_fallback = server_fallback
        self._client = anthropic.Anthropic(
            api_key=api_key, timeout=timeout_s, max_retries=max_retries
        )

    def complete(
        self, *, system: str, user: str, schema: type[BaseModel], max_tokens: int
    ) -> AIResult:
        a = self._anthropic
        base = AIResult(outcome="error", text=None, provider=self.name, model_requested=self.model)
        t0 = time.monotonic()
        try:
            effort: Any = self.effort  # validated by the API; SDK types it as a Literal
            if self.server_fallback:
                # Opt-in only: lets Anthropic re-run a declined request on another model.
                resp: Any = self._client.beta.messages.parse(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                    output_format=schema,
                    output_config={"effort": effort},
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                )
            else:
                resp = self._client.messages.parse(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                    output_format=schema,
                    output_config={"effort": effort},
                )
        except a.APITimeoutError as exc:
            return _fail(base, t0, "timeout", exc)
        except a.AuthenticationError as exc:
            return _fail(base, t0, "not_configured", exc)
        except a.RateLimitError as exc:
            return _fail(base, t0, "rate_limited", exc)
        except a.APIStatusError as exc:
            return _fail(base, t0, "unavailable" if exc.status_code >= 500 else "error", exc)
        except a.APIConnectionError as exc:
            return _fail(base, t0, "unavailable", exc)
        except Exception as exc:  # noqa: BLE001 — e.g. SDK-side parse/validation failure
            return _fail(base, t0, "error", exc)

        base.latency_ms = int((time.monotonic() - t0) * 1000)
        base.model_used = getattr(resp, "model", None)
        base.request_id = getattr(resp, "_request_id", None)
        base.stop_reason = resp.stop_reason
        usage = resp.usage
        base.input_tokens = getattr(usage, "input_tokens", None)
        base.output_tokens = getattr(usage, "output_tokens", None)
        base.cache_read_tokens = getattr(usage, "cache_read_input_tokens", None)
        base.cache_write_tokens = getattr(usage, "cache_creation_input_tokens", None)
        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            base.outcome = "refusal"
            base.error = f"refusal: {getattr(details, 'category', None)}"
            return base
        if resp.stop_reason == "max_tokens":
            base.outcome = "error"
            base.error = "output truncated (max_tokens)"
            return base
        texts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
        base.text = texts[0] if texts else None
        base.outcome = "ok" if base.text else "error"
        if not base.text:
            base.error = "no text block in response"
        return base


def _fail(base: AIResult, t0: float, outcome: str, exc: Exception) -> AIResult:
    base.outcome = outcome
    base.latency_ms = int((time.monotonic() - t0) * 1000)
    base.error = f"{type(exc).__name__}"  # class only: messages can echo request content
    base.request_id = (
        getattr(getattr(exc, "response", None), "headers", {}).get("request-id")
        if getattr(exc, "response", None) is not None
        else None
    )
    return base


class ScriptedProvider:
    """Test double. Each call pops the next scripted reply: a JSON string, or an AIResult
    (to simulate timeouts / outages / refusals). Records every prompt it receives."""

    name = "scripted-test-double"

    def __init__(
        self, replies: list[str | AIResult], model: str = "claude-sonnet-5-5", delay_s: float = 0
    ) -> None:
        self.replies = list(replies)
        self.model = model
        self.prompts: list[tuple[str, str]] = []
        self.delay_s = delay_s

    def complete(
        self, *, system: str, user: str, schema: type[BaseModel], max_tokens: int
    ) -> AIResult:
        self.prompts.append((system, user))
        if self.delay_s:
            time.sleep(self.delay_s)
        reply = self.replies.pop(0)
        if isinstance(reply, AIResult):
            return reply
        return AIResult(
            outcome="ok",
            text=reply,
            provider=self.name,
            model_requested=self.model,
            model_used=self.model,
            latency_ms=1,
            input_tokens=10,
            output_tokens=10,
        )
