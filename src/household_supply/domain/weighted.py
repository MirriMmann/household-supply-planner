"""Mass-priced goods: an exact, *separate* contract from fixed-SKU packs.

Per-kilogram price is a reference rate, NOT a one-kilogram package. A quote
may be estimated from a reference rate; it is not an authorized procurement
plan until a seller-backed minimum and increment exist and the remaining
purchase/household lifecycle has been adapted to actual weighed mass.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_CEILING
from fractions import Fraction

from .money import Money
from .quantity import Quantity


def _require_aware(moment: datetime) -> None:
    if not isinstance(moment, datetime) or moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("weighted market observation must have an aware timestamp")


def _positive_mass(value: Quantity, label: str) -> None:
    if not isinstance(value, Quantity) or value.dimension != "mass" or value.amount <= 0:
        raise ValueError(f"{label} must be positive mass")


def _upper_cent(amount: Fraction) -> Decimal:
    """Budget-safe upper bound in cents, not a prediction of checkout rounding."""
    if amount < 0:
        raise ValueError("weighted price cannot be negative")
    pennies = (Decimal(amount.numerator) / Decimal(amount.denominator) * 100)
    # Exact rational ceil: no dependence on the active decimal context.
    cents = -(-amount.numerator * 100 // amount.denominator)
    return Decimal(cents) / Decimal(100)


@dataclass(frozen=True, slots=True)
class WeightedPriceObservation:
    item_id: str
    source_ref: str
    observed_at: datetime
    price: Money | None  # price for reference_mass; None means unavailable
    reference_mass: Quantity
    available: bool

    def __post_init__(self) -> None:
        if not isinstance(self.item_id, str) or not self.item_id.strip():
            raise ValueError("weighted observation needs a canonical Item identity")
        if not isinstance(self.source_ref, str) or not self.source_ref.strip():
            raise ValueError("weighted observation needs a source reference")
        _require_aware(self.observed_at)
        _positive_mass(self.reference_mass, "reference_mass")
        if type(self.available) is not bool:
            raise ValueError("weighted availability must be bool")
        if self.available:
            if not isinstance(self.price, Money) or self.price.amount < 0:
                raise ValueError("available weighted observation requires nonnegative price")
        elif self.price is not None:
            raise ValueError("unavailable weighted observation must not carry a price")


@dataclass(frozen=True, slots=True)
class WeightedPriceEstimate:
    observation: WeightedPriceObservation
    requested: Quantity
    estimated_upper_bound: Money | None
    is_purchase_quote: bool = False

    def __post_init__(self) -> None:
        _positive_mass(self.requested, "requested")
        if self.is_purchase_quote:
            raise ValueError("unit-price reference estimate is never an actionable purchase")
        expected = estimate_weight_cost(self.observation, self.requested)
        if expected != self.estimated_upper_bound:
            raise ValueError("weighted estimate is inconsistent with its price evidence")


def estimate_weight_cost(
    observation: WeightedPriceObservation, requested: Quantity
) -> Money | None:
    """Conservative cost estimate for a chosen mass, not a quantity you can buy."""
    _positive_mass(requested, "requested")
    if not observation.available:
        return None
    assert observation.price is not None
    cost = (
        Fraction(observation.price.amount)
        * Fraction(requested.base_amount)
        / Fraction(observation.reference_mass.base_amount)
    )
    return Money(_upper_cent(cost), observation.price.currency)


@dataclass(frozen=True, slots=True)
class VerifiedWeightSaleRule:
    """Explicit retailer-verified sellability, NEVER inferred from 'per kg'.

    Valid amounts are minimum + N * increment for N >= 0. The source must
    separately evidence both minimum and increment (not simply a unit price).
    """

    item_id: str
    source_ref: str
    verified_at: datetime
    minimum: Quantity
    increment: Quantity

    def __post_init__(self) -> None:
        if not isinstance(self.item_id, str) or not self.item_id.strip():
            raise ValueError("weight rule requires an Item identity")
        if not isinstance(self.source_ref, str) or not self.source_ref.strip():
            raise ValueError("weight rule requires source evidence")
        _require_aware(self.verified_at)
        _positive_mass(self.minimum, "minimum")
        _positive_mass(self.increment, "increment")


@dataclass(frozen=True, slots=True)
class VerifiedWeightQuote:
    observation: WeightedPriceObservation
    rule: VerifiedWeightSaleRule
    requested: Quantity
    sellable: Quantity
    estimated_upper_bound: Money

    def __post_init__(self) -> None:
        _positive_mass(self.requested, "requested")
        _positive_mass(self.sellable, "sellable")
        if not self.observation.available:
            raise ValueError("cannot quote unavailable weight listing")
        if (
            self.observation.item_id != self.rule.item_id
            or self.observation.source_ref != self.rule.source_ref
        ):
            raise ValueError("sale rule and unit-price observation refer to different listing")
        if self.rule.verified_at > self.observation.observed_at:
            raise ValueError("sale rule comes from later than the price observation")
        expected = quote_verified_mass(self.observation, self.rule, self.requested)
        if expected.sellable != self.sellable or expected.estimated_upper_bound != self.estimated_upper_bound:
            raise ValueError("weighted quote violates seller sellability or cost evidence")


def quote_verified_mass(
    observation: WeightedPriceObservation,
    rule: VerifiedWeightSaleRule,
    requested: Quantity,
) -> VerifiedWeightQuote:
    """Produce a deterministic price quote ONLY with explicit sellability evidence."""
    _positive_mass(requested, "requested")
    if not observation.available:
        raise ValueError("cannot quote an unavailable weighted listing")
    if observation.item_id != rule.item_id or observation.source_ref != rule.source_ref:
        raise ValueError("mismatched item or listing evidence")
    if rule.verified_at > observation.observed_at:
        raise ValueError("sellability evidence is newer than its price observation")
    target = Fraction(requested.base_amount)
    minimum = Fraction(rule.minimum.base_amount)
    increment = Fraction(rule.increment.base_amount)
    steps = max(0, -(-(target - minimum) // increment))
    sellable = Quantity(Decimal(minimum.numerator) / Decimal(minimum.denominator) +
                        Decimal(steps) * rule.increment.base_amount, "g")
    # This constructor is used for structural validation, without recursion.
    return object.__new__(VerifiedWeightQuote)  # overwritten below
