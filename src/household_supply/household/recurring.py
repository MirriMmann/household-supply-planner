from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from household_supply.demand import DemandContribution
from household_supply.domain import Quantity
from household_supply.domain._decimal import (
    multiply_decimal_by_int_exact,
    scale_decimal_ratio_up,
)
from household_supply.domain.money import DecimalLike, as_decimal

from .learning import ConsumptionEstimate


RECURRING_NEED_DECIMAL_PLACES = 12


@dataclass(frozen=True, slots=True)
class RecurringNeedSource:
    """DemandSource backed by explicit, inspectable usage/depletion estimates."""

    source_id: str
    horizon_days: Decimal
    estimates: tuple[ConsumptionEstimate, ...]

    def __init__(
        self,
        source_id: str,
        horizon_days: DecimalLike,
        estimates: tuple[ConsumptionEstimate, ...],
    ) -> None:
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "horizon_days", as_decimal(horizon_days))
        object.__setattr__(self, "estimates", tuple(estimates))
        self.__post_init__()

    def __post_init__(self) -> None:
        source_id = self.source_id.strip()
        if not source_id:
            raise ValueError("recurring need source id must not be empty")
        if self.horizon_days <= 0:
            raise ValueError("recurring need horizon_days must be positive")
        estimates = tuple(self.estimates)
        if not estimates:
            raise ValueError("recurring need source requires at least one estimate")
        item_ids = [estimate.item.id for estimate in estimates]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("recurring need source contains duplicate item estimates")
        estimates = tuple(sorted(estimates, key=lambda estimate: estimate.item.id))
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "estimates", estimates)

    def emit_contributions(self) -> tuple[DemandContribution, ...]:
        contributions = []
        for estimate in self.estimates:
            consumed = estimate.total_depleted.as_base()
            day_microseconds = multiply_decimal_by_int_exact(
                self.horizon_days, 86_400_000_000
            )
            amount = scale_decimal_ratio_up(
                consumed.base_amount,
                day_microseconds,
                Decimal(estimate.observed_microseconds),
                decimal_places=RECURRING_NEED_DECIMAL_PLACES,
            )
            contributions.append(
                DemandContribution(
                    source_id=self.source_id,
                    contribution_id=f"recurring:{estimate.item.id}",
                    item=estimate.item,
                    quantity=Quantity(amount, consumed.base_unit),
                )
            )
        return tuple(contributions)
