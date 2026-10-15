import pytest

from outcomeguard.money import Money, MoneyError


def test_round_trip_usd():
    m = Money.from_decimal_str("500.00", "USD")
    assert m.minor == 50000
    assert m.to_paypal() == {"currency_code": "USD", "value": "500.00"}


def test_no_float_drift():
    # 0.1 + 0.2 style errors cannot happen: amounts are integers.
    a = Money.from_decimal_str("0.10", "USD").minor + Money.from_decimal_str("0.20", "USD").minor
    assert Money(a, "USD").to_decimal_str() == "0.30"


def test_rejects_sub_cent_and_garbage():
    for bad in ("1.005", "abc", "NaN", "Infinity", "-1.00"):
        with pytest.raises(MoneyError):
            Money.from_decimal_str(bad, "USD")


def test_zero_decimal_currency():
    assert Money.from_decimal_str("500", "JPY").to_decimal_str() == "500"
    with pytest.raises(MoneyError):
        Money.from_decimal_str("1.5", "JPY")


def test_rejects_float_and_unknown_currency():
    with pytest.raises(MoneyError):
        Money(1.5, "USD")  # type: ignore[arg-type]
    with pytest.raises(MoneyError):
        Money(100, "XYZ")
