from decimal import Decimal

import pytest

from household_supply.domain.items import Item
from household_supply.domain.money import Money
from household_supply.pricing.catalog import EstimatedPrice
from household_supply.pricing.estimate import (
    PriceEstimate,
    estimate_price,
)


@pytest.fixture
def chicken() -> Item:
    return Item(
        id="chicken_breast",
        canonical_name="Куриное филе",
        category="meat",
    )


@pytest.fixture
def chicken_price(chicken: Item) -> EstimatedPrice:
    return EstimatedPrice(
        item=chicken,
        min_price=Money("300", "KGS"),
        max_price=Money("400", "KGS"),
    )


def test_estimate_price(chicken_price: EstimatedPrice) -> None:
    result = estimate_price(
        chicken_price,
        Decimal("1.5"),
    )

    assert isinstance(result, PriceEstimate)
    assert result.min_price.amount == Decimal("450")
    assert result.max_price.amount == Decimal("600")
    assert result.min_price.currency == "KGS"


def test_estimate_zero_quantity(
    chicken_price: EstimatedPrice,
) -> None:
    result = estimate_price(
        chicken_price,
        Decimal("0"),
    )

    assert result.min_price.amount == Decimal("0")
    assert result.max_price.amount == Decimal("0")


def test_negative_quantity_is_rejected(
    chicken_price: EstimatedPrice,
) -> None:
    with pytest.raises(ValueError):
        estimate_price(
            chicken_price,
            Decimal("-1"),
        )


def test_fractional_quantity(
    chicken_price: EstimatedPrice,
) -> None:
    result = estimate_price(
        chicken_price,
        Decimal("0.25"),
    )

    assert result.min_price.amount == Decimal("75")
    assert result.max_price.amount == Decimal("100")
