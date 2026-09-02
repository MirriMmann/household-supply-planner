from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from fractions import Fraction

from household_supply.domain import Item, Quantity
from household_supply.domain._decimal import (
    add_decimals_exact,
    decimal_from_coefficient,
    subtract_decimals_exact,
)

from .events import ConsumptionObservation
from .history import HouseholdHistory


ESTIMATE_DECIMAL_PLACES = 12
_MICROSECONDS_PER_DAY = 86_400_000_000


class ConsumptionEstimationError(ValueError):
    """Consumption evidence cannot be interpreted without double counting."""


def _duration_microseconds(start: datetime, end: datetime) -> int:
    delta = end - start
    return (
        delta.days * _MICROSECONDS_PER_DAY
        + delta.seconds * 1_000_000
        + delta.microseconds
    )


def _fraction_to_decimal_half_even(
    value: Fraction, *, decimal_places: int = ESTIMATE_DECIMAL_PLACES
) -> Decimal:
    if value < 0:
        raise ValueError("consumption estimate fraction must not be negative")
    scale = 10**decimal_places
    numerator = value.numerator * scale
    quotient, remainder = divmod(numerator, value.denominator)
    doubled = remainder * 2
    if doubled > value.denominator or (
        doubled == value.denominator and quotient % 2 == 1
    ):
        quotient += 1
    return decimal_from_coefficient(quotient, -decimal_places)


@dataclass(frozen=True, slots=True)
class ConsumptionEstimate:
    """Transparent descriptive estimate derived only from recorded observations.

    ``uncertainty`` is the observed max-minus-min daily-rate spread. It is not a
    confidence interval or probabilistic guarantee.
    """

    item: Item
    daily_quantity: Quantity
    sample_count: int
    observed_days: Decimal
    total_consumed: Quantity
    observed_microseconds: int
    daily_min: Quantity
    daily_max: Quantity
    uncertainty: Quantity

    def __post_init__(self) -> None:
        if type(self.sample_count) is not int or self.sample_count <= 0:
            raise ValueError("consumption estimate sample_count must be positive")
        if self.observed_days <= 0:
            raise ValueError("consumption estimate observed_days must be positive")
        if self.total_consumed.amount <= 0:
            raise ValueError("consumption estimate total_consumed must be positive")
        if type(self.observed_microseconds) is not int or self.observed_microseconds <= 0:
            raise ValueError("consumption estimate observed_microseconds must be positive")
        quantities = (
            self.daily_quantity,
            self.total_consumed,
            self.daily_min,
            self.daily_max,
            self.uncertainty,
        )
        if not all(self.daily_quantity.compatible_with(value) for value in quantities):
            raise ValueError("consumption estimate quantities must use one dimension")
        if self.daily_min.base_amount > self.daily_quantity.base_amount:
            raise ValueError("consumption estimate daily_min exceeds daily_quantity")
        if self.daily_quantity.base_amount > self.daily_max.base_amount:
            raise ValueError("consumption estimate daily_quantity exceeds daily_max")
        expected_uncertainty = subtract_decimals_exact(
            self.daily_max.base_amount, self.daily_min.base_amount
        )
        if self.uncertainty.base_amount != expected_uncertainty:
            raise ValueError("consumption estimate uncertainty must equal max-min spread")
        expected_daily = _fraction_to_decimal_half_even(
            Fraction(self.total_consumed.base_amount)
            * _MICROSECONDS_PER_DAY
            / self.observed_microseconds
        )
        if self.daily_quantity.base_amount != expected_daily:
            raise ValueError(
                "consumption estimate daily_quantity does not match exact evidence basis"
            )
        expected_days = _fraction_to_decimal_half_even(
            Fraction(self.observed_microseconds, _MICROSECONDS_PER_DAY)
        )
        if self.observed_days != expected_days:
            raise ValueError(
                "consumption estimate observed_days does not match exact duration basis"
            )


def _observations_for_item(
    history: HouseholdHistory,
    item_id: str,
    *,
    as_of: datetime | None,
) -> tuple[ConsumptionObservation, ...]:
    normalized = item_id.strip()
    if not normalized:
        raise ValueError("consumption estimate item_id must not be empty")
    if as_of is not None and (as_of.tzinfo is None or as_of.utcoffset() is None):
        raise ValueError("consumption estimate as_of must be timezone-aware")

    observations = tuple(
        event
        for event in history.events
        if isinstance(event, ConsumptionObservation)
        and event.item.id == normalized
        and (
            as_of is None
            or (event.recorded_at <= as_of and event.period_end <= as_of)
        )
    )
    return observations


def estimate_consumption(
    history: HouseholdHistory,
    item_id: str,
    *,
    as_of: datetime | None = None,
) -> ConsumptionEstimate | None:
    observations = list(_observations_for_item(history, item_id, as_of=as_of))
    if not observations:
        return None

    item = observations[0].item
    base_unit = observations[0].quantity_consumed.base_unit
    for observation in observations:
        if observation.item != item:
            raise ConsumptionEstimationError(
                f"conflicting consumption Item identity: {item_id}"
            )
        if observation.quantity_consumed.base_unit != base_unit:
            raise ConsumptionEstimationError(
                f"incompatible consumption units for item: {item_id}"
            )

    observations.sort(
        key=lambda observation: (
            observation.period_start,
            observation.period_end,
            observation.event_id.value,
        )
    )
    previous: ConsumptionObservation | None = None
    rates: list[Fraction] = []
    total_base_amount = Decimal(0)
    total_duration = 0
    for observation in observations:
        if previous is not None and observation.period_start < previous.period_end:
            raise ConsumptionEstimationError(
                "overlapping consumption observations for item "
                f"{item_id}: {previous.event_id} and {observation.event_id}"
            )
        duration = _duration_microseconds(
            observation.period_start, observation.period_end
        )
        if duration <= 0:  # guarded by event construction
            raise AssertionError("consumption duration invariant violated")
        base_amount = observation.quantity_consumed.base_amount
        consumed = Fraction(base_amount)
        rates.append(consumed * _MICROSECONDS_PER_DAY / duration)
        total_base_amount = add_decimals_exact(total_base_amount, base_amount)
        total_duration += duration
        previous = observation

    weighted_daily = (
        Fraction(total_base_amount) * _MICROSECONDS_PER_DAY / total_duration
    )
    daily_min_fraction = min(rates)
    daily_max_fraction = max(rates)

    daily_amount = _fraction_to_decimal_half_even(weighted_daily)
    daily_min = _fraction_to_decimal_half_even(daily_min_fraction)
    daily_max = _fraction_to_decimal_half_even(daily_max_fraction)
    uncertainty = subtract_decimals_exact(daily_max, daily_min)
    observed_days = _fraction_to_decimal_half_even(
        Fraction(total_duration, _MICROSECONDS_PER_DAY)
    )

    return ConsumptionEstimate(
        item=item,
        daily_quantity=Quantity(daily_amount, base_unit),
        sample_count=len(observations),
        observed_days=observed_days,
        total_consumed=Quantity(total_base_amount, base_unit),
        observed_microseconds=total_duration,
        daily_min=Quantity(daily_min, base_unit),
        daily_max=Quantity(daily_max, base_unit),
        uncertainty=Quantity(uncertainty, base_unit),
    )


def estimate_all_consumption(
    history: HouseholdHistory,
    *,
    as_of: datetime | None = None,
) -> tuple[ConsumptionEstimate, ...]:
    item_ids = sorted(
        {
            event.item.id
            for event in history.events
            if isinstance(event, ConsumptionObservation)
            and (
                as_of is None
                or (event.recorded_at <= as_of and event.period_end <= as_of)
            )
        }
    )
    estimates = []
    for item_id in item_ids:
        estimate = estimate_consumption(history, item_id, as_of=as_of)
        if estimate is not None:
            estimates.append(estimate)
    return tuple(estimates)
