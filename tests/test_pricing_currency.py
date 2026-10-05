from decimal import Decimal

import pytest

from decimal import Decimal
from household_supply.pricing.currency import convert_currency
from household_supply.domain.money import Money
from household_supply.pricing.estimate import PriceEstimate
from household_supply.pricing.currency import (
    convert_estimate,
    convert_money,
)



def test_convert_currency_from_kgs() -> None:
    result = convert_currency(
        Money("1000", "KGS"),
        "USD",
    )

    assert result.currency == "USD"
    assert result.amount == Decimal("11.5000")


def test_convert_currency_to_kgs() -> None:
    result = convert_currency(
        Money("10", "USD"),
        "KGS",
    )

    assert result.currency == "KGS"
    assert result.amount == Decimal("869.5652173913043478260869565")


def test_same_currency_is_unchanged() -> None:
    money = Money("1000", "KGS")

    result = convert_currency(money, "KGS")

    assert result == money


def test_unsupported_currency_is_rejected() -> None:
    with pytest.raises(ValueError):
        convert_currency(
            Money("1000", "KGS"),
            "JPY",
        )

def test_convert_money() -> None:
    result = convert_money(
        Money("10", "USD"),
        Decimal("87.5"),
        "KGS",
    )

    assert result.amount == Decimal("875")
    assert result.currency == "KGS"


def test_convert_money_fractional_value() -> None:
    result = convert_money(
        Money("3.4", "USD"),
        Decimal("87.5"),
        "KGS",
    )

    assert result.amount == Decimal("297.50")
    assert result.currency == "KGS"


def test_convert_estimate() -> None:
    estimate = PriceEstimate(
        min_price=Money("3.4", "USD"),
        max_price=Money("4.6", "USD"),
    )

    result = convert_estimate(
        estimate,
        Decimal("87.5"),
        "KGS",
    )

    assert result.min_price.amount == Decimal("297.50")
    assert result.max_price.amount == Decimal("402.50")
    assert result.min_price.currency == "KGS"
    assert result.max_price.currency == "KGS"


def test_zero_amount() -> None:
    result = convert_money(
        Money("0", "USD"),
        Decimal("87.5"),
        "KGS",
    )

    assert result.amount == Decimal("0")
    assert result.currency == "KGS"


def test_negative_rate_is_rejected() -> None:
    with pytest.raises(ValueError):
        convert_money(
            Money("10", "USD"),
            Decimal("-1"),
            "KGS",
        )


def test_zero_rate_is_rejected() -> None:
    with pytest.raises(ValueError):
        convert_money(
            Money("10", "USD"),
            Decimal("0"),
            "KGS",
        )


def test_empty_target_currency_is_rejected() -> None:
    with pytest.raises(ValueError):
        convert_money(
            Money("10", "USD"),
            Decimal("87.5"),
            " ",
        )
