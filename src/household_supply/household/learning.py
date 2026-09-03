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
    """Transparent descriptive rate estimate derived from explicit evidence.

    The historical field name ``total_consumed`` is retained for compatibility.
    M10 depletion learning may also use this structure for positive/zero supply
    depletion evidence; the estimate is still an inspectable deterministic rate,
    not a probabilistic confidence claim.
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

    @property
    def total_depleted(self) -> Quantity:
        """Product-level name for the exact positive depletion evidence basis."""

        return self.total_consumed


@dataclass(frozen=True, slots=True)
class UsageRateSample:
    """One non-negative, bounded piece of usage/depletion rate evidence."""

    evidence_id: str
    item: Item
    quantity: Quantity
    period_start: datetime
    period_end: datetime

    def __post_init__(self) -> None:
        evidence_id = self.evidence_id.strip()
        if not evidence_id:
            raise ValueError("usage-rate evidence id must not be empty")
        if self.quantity.amount < 0:
            raise ValueError("usage-rate evidence quantity must not be negative")
        for label, value in (
            ("period_start", self.period_start),
            ("period_end", self.period_end),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"usage-rate evidence {label} must be timezone-aware")
        if self.period_end <= self.period_start:
            raise ValueError("usage-rate evidence period_end must be after period_start")
        object.__setattr__(self, "evidence_id", evidence_id)


def estimate_usage_rate(
    samples: tuple[UsageRateSample, ...],
) -> ConsumptionEstimate | None:
    """Estimate one rate from non-overlapping evidence using exact arithmetic.

    Zero-quantity samples contribute observed time (important for depletion
    learning) but an all-zero evidence set produces no recurring estimate.
    """

    samples = tuple(samples)
    if not samples:
        return None

    ordered = sorted(
        samples,
        key=lambda sample: (sample.period_start, sample.period_end, sample.evidence_id),
    )
    item = ordered[0].item
    base_unit = ordered[0].quantity.base_unit
    previous: UsageRateSample | None = None
    rates: list[Fraction] = []
    total_base_amount = Decimal(0)
    total_duration = 0

    for sample in ordered:
        if sample.item != item:
            raise ConsumptionEstimationError(
                f"conflicting consumption Item identity: {item.id}"
            )
        if sample.quantity.base_unit != base_unit:
            raise ConsumptionEstimationError(
                f"incompatible consumption units for item: {item.id}"
            )
        if previous is not None and sample.period_start < previous.period_end:
            raise ConsumptionEstimationError(
                "overlapping consumption evidence for item "
                f"{item.id}: {previous.evidence_id} and {sample.evidence_id}"
            )
        duration = _duration_microseconds(sample.period_start, sample.period_end)
        if duration <= 0:  # guarded by sample construction
            raise AssertionError("usage-rate duration invariant violated")
        base_amount = sample.quantity.base_amount
        rates.append(Fraction(base_amount) * _MICROSECONDS_PER_DAY / duration)
        total_base_amount = add_decimals_exact(total_base_amount, base_amount)
        total_duration += duration
        previous = sample

    if total_base_amount == 0:
        return None

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
        sample_count=len(ordered),
        observed_days=observed_days,
        total_consumed=Quantity(total_base_amount, base_unit),
        observed_microseconds=total_duration,
        daily_min=Quantity(daily_min, base_unit),
        daily_max=Quantity(daily_max, base_unit),
        uncertainty=Quantity(uncertainty, base_unit),
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
    observations = _observations_for_item(history, item_id, as_of=as_of)
    samples = tuple(
        UsageRateSample(
            evidence_id=observation.event_id.value,
            item=observation.item,
            quantity=observation.quantity_consumed,
            period_start=observation.period_start,
            period_end=observation.period_end,
        )
        for observation in observations
    )
    return estimate_usage_rate(samples)


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
