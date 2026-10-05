from __future__ import annotations

from decimal import Decimal

from ..domain.money import Money
from .estimate import PriceEstimate

DEFAULT_RATES_FROM_KGS: dict[str, Decimal] = {
    "KGS": Decimal("1"),
    "KZT": Decimal("5.5"),
    "USD": Decimal("0.0115"),
    "EUR": Decimal("0.0098"),
}

def convert_currency(
    money: Money,
    target_currency: str,
    rates_from_kgs: dict[str, Decimal] = DEFAULT_RATES_FROM_KGS,
) -> Money:
    """Convert money using rates expressed relative to KGS."""

    target_currency = target_currency.strip().upper()

    if not target_currency:
        raise ValueError("target_currency must not be empty")

    if money.currency == target_currency:
        return money

    if money.currency not in rates_from_kgs:
        raise ValueError(
            f"unsupported source currency: {money.currency}"
        )

    if target_currency not in rates_from_kgs:
        raise ValueError(
            f"unsupported target currency: {target_currency}"
        )

    source_rate = rates_from_kgs[money.currency]
    target_rate = rates_from_kgs[target_currency]

    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("currency rates must be greater than zero")

    amount_in_kgs = money.amount / source_rate
    converted_amount = amount_in_kgs * target_rate

    return Money(converted_amount, target_currency)

def convert_money(
    money: Money,
    rate: Decimal,
    target_currency: str,
) -> Money:
    """
    Convert a monetary value using an explicitly supplied exchange rate.

    The rate means:

        1 source currency = rate target currency.
    """

    if rate <= 0:
        raise ValueError("exchange rate must be greater than zero")

    if not target_currency.strip():
        raise ValueError("target_currency must not be empty")

    converted_amount = money.amount * rate

    return Money(
        converted_amount,
        target_currency,
    )


def convert_estimate(
    estimate: PriceEstimate,
    rate: Decimal,
    target_currency: str,
) -> PriceEstimate:
    """
    Convert a complete price estimate into another currency.
    """

    min_price = convert_money(
        estimate.min_price,
        rate,
        target_currency,
    )

    max_price = convert_money(
        estimate.max_price,
        rate,
        target_currency,
    )

    return PriceEstimate(
        min_price=min_price,
        max_price=max_price,
    )
