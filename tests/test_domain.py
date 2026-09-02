from __future__ import annotations

from decimal import Decimal

import pytest

from household_supply.domain import CurrencyMismatchError, Money, Quantity


def test_money_uses_exact_decimal_representation() -> None:
    assert (Money("0.1", "KGS") + Money("0.2", "KGS")).amount == Decimal("0.3")


def test_money_rejects_cross_currency_arithmetic() -> None:
    with pytest.raises(CurrencyMismatchError):
        _ = Money(1, "KGS") + Money(1, "USD")


def test_quantity_converts_mass_exactly() -> None:
    assert Quantity("1.25", "kg").to("g") == Quantity("1250", "g")


def test_quantity_rejects_cross_dimension_conversion() -> None:
    with pytest.raises(ValueError, match="incompatible quantity units"):
        Quantity(1, "kg").to("l")
