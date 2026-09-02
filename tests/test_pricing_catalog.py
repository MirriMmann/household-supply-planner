from decimal import Decimal

import pytest

from household_supply.domain.items import Item
from household_supply.domain.money import Money
from household_supply.pricing.catalog import (
    EstimatedPrice,
    PriceCatalog,
)


@pytest.fixture
def chicken() -> Item:
    return Item(
        id="chicken_breast",
        canonical_name="Куриное филе",
        category="meat",
    )


def test_estimated_price_is_created(chicken: Item) -> None:
    price = EstimatedPrice(
        item=chicken,
        min_price=Money("300", "KGS"),
        max_price=Money("400", "KGS"),
    )

    assert price.item.id == "chicken_breast"
    assert price.min_price.amount == Decimal("300")
    assert price.max_price.amount == Decimal("400")


def test_catalog_returns_price(chicken: Item) -> None:
    price = EstimatedPrice(
        item=chicken,
        min_price=Money("300", "KGS"),
        max_price=Money("400", "KGS"),
    )

    catalog = PriceCatalog(prices=(price,))

    result = catalog.get(chicken)

    assert result == price


def test_catalog_contains_item(chicken: Item) -> None:
    price = EstimatedPrice(
        item=chicken,
        min_price=Money("300", "KGS"),
        max_price=Money("400", "KGS"),
    )

    catalog = PriceCatalog(prices=(price,))

    assert catalog.contains(chicken)


def test_catalog_does_not_contain_unknown_item(chicken: Item) -> None:
    catalog = PriceCatalog(prices=())

    assert not catalog.contains(chicken)


def test_invalid_price_range_is_rejected(chicken: Item) -> None:
    with pytest.raises(ValueError):
        EstimatedPrice(
            item=chicken,
            min_price=Money("400", "KGS"),
            max_price=Money("300", "KGS"),
        )


def test_different_currencies_are_rejected(chicken: Item) -> None:
    with pytest.raises(ValueError):
        EstimatedPrice(
            item=chicken,
            min_price=Money("300", "KGS"),
            max_price=Money("10", "USD"),
        )


def test_duplicate_items_are_rejected(chicken: Item) -> None:
    price_1 = EstimatedPrice(
        item=chicken,
        min_price=Money("300", "KGS"),
        max_price=Money("400", "KGS"),
    )

    price_2 = EstimatedPrice(
        item=chicken,
        min_price=Money("350", "KGS"),
        max_price=Money("450", "KGS"),
    )

    with pytest.raises(ValueError):
        PriceCatalog(prices=(price_1, price_2))
