"""Model pricing used for LOCAL cost estimates and budget enforcement.

These are estimates computed from reported token usage and the published price list —
they are NOT Anthropic billing data. The Anthropic Console is authoritative. Use an
account-level spend limit in the Console as an additional safeguard.

Prices: USD per million tokens, from the official pricing page.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from decimal import Decimal

PRICING_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"
PRICING_RETRIEVED = "2026-10-15"


@dataclass(frozen=True)
class ModelPrice:
    model: str
    input_per_mtok: Decimal
    output_per_mtok: Decimal
    cache_write_5m_per_mtok: Decimal
    cache_read_per_mtok: Decimal
    source: str = PRICING_SOURCE
    effective: str = PRICING_RETRIEVED  # date the table was read; see source for terms

    def as_dict(self) -> dict[str, str]:
        return {
            "model": self.model,
            "input_per_mtok": str(self.input_per_mtok),
            "output_per_mtok": str(self.output_per_mtok),
            "cache_write_5m_per_mtok": str(self.cache_write_5m_per_mtok),
            "cache_read_per_mtok": str(self.cache_read_per_mtok),
            "source": self.source,
            "effective": self.effective,
        }


def _p(model: str, i: str, o: str, cw: str, cr: str) -> ModelPrice:
    return ModelPrice(model, Decimal(i), Decimal(o), Decimal(cw), Decimal(cr))


# Rows copied from the official pricing table (base input, output, 5m cache write, cache hit).
DEFAULT_PRICES: dict[str, ModelPrice] = {
    "claude-sonnet-5-5": _p("claude-sonnet-5-5", "2", "10", "2.50", "0.10"),
    "claude-sonnet-5": _p("claude-sonnet-5", "2", "10", "2.50", "0.20"),
    "claude-opus-5": _p("claude-opus-5", "5", "25", "6.25", "0.50"),
}

MTOK = Decimal(1_000_000)


def price_for(model: str) -> ModelPrice | None:
    """Pricing for a model; AI_PRICING_JSON can override/add rows. None → unknown price,
    and callers must fail closed."""
    raw = os.environ.get("AI_PRICING_JSON")
    if raw:
        for row in json.loads(raw):
            if row["model"] == model:
                return ModelPrice(
                    model,
                    Decimal(row["input_per_mtok"]),
                    Decimal(row["output_per_mtok"]),
                    Decimal(row.get("cache_write_5m_per_mtok", row["input_per_mtok"])),
                    Decimal(row.get("cache_read_per_mtok", row["input_per_mtok"])),
                    row.get("source", "AI_PRICING_JSON"),
                    row.get("effective", "configured"),
                )
    return DEFAULT_PRICES.get(model)


def conservative_input_tokens(*texts: str) -> int:
    """Upper-bound guess of prompt tokens without calling the API: 1 token per 2 UTF-8
    bytes (real tokenisers average ~3–4 bytes/token) plus fixed overhead for the
    structured-output machinery."""
    return math.ceil(sum(len(t.encode()) for t in texts) / 2) + 1500


def max_cost(
    price: ModelPrice, input_tokens: int, max_output_tokens: int, attempts: int
) -> Decimal:
    """Worst case for one logical request: every attempt (retries included) uses the full
    estimated input and the full output allowance."""
    one = (
        Decimal(input_tokens) * price.input_per_mtok
        + Decimal(max_output_tokens) * price.output_per_mtok
    ) / MTOK
    return (one * attempts).quantize(Decimal("0.000001"))


def usage_cost(
    price: ModelPrice,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> Decimal:
    cost = (
        Decimal(input_tokens) * price.input_per_mtok
        + Decimal(output_tokens) * price.output_per_mtok
        + Decimal(cache_read_tokens) * price.cache_read_per_mtok
        + Decimal(cache_write_tokens) * price.cache_write_5m_per_mtok
    ) / MTOK
    return cost.quantize(Decimal("0.000001"))
