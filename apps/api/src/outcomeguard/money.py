"""Exact money handling.

Amounts are stored as integer minor units (cents). PayPal speaks decimal strings
("500.00"); conversion happens only at the API boundary, with `Decimal`, never `float`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# ISO-4217 exponents for currencies PayPal supports with a non-2 exponent.
_ZERO_DECIMAL = {"HUF", "JPY", "TWD"}
SUPPORTED_CURRENCIES = {"USD", "EUR", "GBP", "AUD", "CAD", "JPY"}


class MoneyError(ValueError):
    pass


def exponent(currency: str) -> int:
    return 0 if currency.upper() in _ZERO_DECIMAL else 2


@dataclass(frozen=True, slots=True)
class Money:
    minor: int
    currency: str

    def __post_init__(self) -> None:
        if not isinstance(self.minor, int) or isinstance(self.minor, bool):
            raise MoneyError("minor units must be an int")
        if self.minor < 0:
            raise MoneyError("amount must not be negative")
        if self.currency not in SUPPORTED_CURRENCIES:
            raise MoneyError(f"unsupported currency {self.currency!r}")

    @classmethod
    def from_decimal_str(cls, value: str, currency: str) -> Money:
        currency = currency.upper()
        try:
            dec = Decimal(value)
        except (InvalidOperation, TypeError) as exc:
            raise MoneyError(f"invalid amount {value!r}") from exc
        if not dec.is_finite():
            raise MoneyError(f"invalid amount {value!r}")
        exp = exponent(currency)
        scaled = dec.scaleb(exp)
        if scaled != scaled.to_integral_value():
            raise MoneyError(f"{value!r} has too many decimals for {currency}")
        return cls(int(scaled), currency)

    def to_decimal_str(self) -> str:
        exp = exponent(self.currency)
        if exp == 0:
            return str(self.minor)
        return f"{Decimal(self.minor).scaleb(-exp):.{exp}f}"

    def to_paypal(self) -> dict[str, str]:
        return {"currency_code": self.currency, "value": self.to_decimal_str()}

    @classmethod
    def from_paypal(cls, amount: dict[str, str]) -> Money:
        return cls.from_decimal_str(amount["value"], amount["currency_code"])

    def __str__(self) -> str:
        return f"{self.to_decimal_str()} {self.currency}"
