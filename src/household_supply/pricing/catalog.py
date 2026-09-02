from __future__ import annotations

from dataclasses import dataclass

from ..domain.items import Item
from ..domain.money import Money


@dataclass(frozen=True, slots=True)
class EstimatedPrice:
    """Approximate market price range for a canonical Item."""

    item: Item
    min_price: Money
    max_price: Money

    def __post_init__(self) -> None:
        if self.min_price.currency != self.max_price.currency:
            raise ValueError(
                "min_price and max_price must use the same currency"
            )

        if self.min_price.amount < 0:
            raise ValueError("min_price must not be negative")

        if self.max_price.amount < 0:
            raise ValueError("max_price must not be negative")

        if self.min_price.amount > self.max_price.amount:
            raise ValueError(
                "min_price must not be greater than max_price"
            )


@dataclass(frozen=True, slots=True)
class PriceCatalog:
    """Immutable collection of approximate prices indexed by Item ID."""

    prices: tuple[EstimatedPrice, ...]

    def __post_init__(self) -> None:
        prices = tuple(self.prices)

        seen: set[str] = set()

        for estimated_price in prices:
            item_id = estimated_price.item.id

            if item_id in seen:
                raise ValueError(
                    f"duplicate estimated price for item: {item_id}"
                )

            seen.add(item_id)

        object.__setattr__(self, "prices", prices)

    def get(self, item: Item) -> EstimatedPrice:
        """Return the estimated price for an Item."""

        for estimated_price in self.prices:
            if estimated_price.item.id == item.id:
                return estimated_price

        raise KeyError(
            f"estimated price not found for item: {item.id}"
        )

    def contains(self, item: Item) -> bool:
        """Return True if the catalog contains an estimate for an Item."""

        return any(
            estimated_price.item.id == item.id
            for estimated_price in self.prices
        )

    def __len__(self) -> int:
        return len(self.prices)