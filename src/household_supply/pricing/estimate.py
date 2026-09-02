from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..domain.money import Money
from .catalog import EstimatedPrice


@dataclass(frozen=True, slots=True)
class PriceEstimate:
    """Estimated total price range for a requested quantity."""

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


def estimate_price(
    estimated_price: EstimatedPrice,
    quantity: Decimal,
) -> PriceEstimate:
    """
    Estimate total cost for the requested quantity.

    The quantity is expressed in the same unit as the catalog price.
    """

    if quantity < 0:
        raise ValueError("quantity must not be negative")

    min_amount = estimated_price.min_price.amount * quantity
    max_amount = estimated_price.max_price.amount * quantity

    return PriceEstimate(
        min_price=Money(
            min_amount,
            estimated_price.min_price.currency,
        ),
        max_price=Money(
            max_amount,
            estimated_price.max_price.currency,
        ),
    )
