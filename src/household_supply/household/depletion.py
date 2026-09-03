from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from household_supply.domain import Item, Quantity
from household_supply.domain._decimal import add_decimals_exact, subtract_decimals_exact

from .events import ConsumptionObservation, InventoryCorrection, PurchaseEvent
from .history import HouseholdHistory
from .learning import (
    ConsumptionEstimate,
    ConsumptionEstimationError,
    UsageRateSample,
    estimate_usage_rate,
)


class DepletionWindowStatus(str, Enum):
    USED = "used"
    ZERO_DEPLETION = "zero_depletion"
    UNEXPLAINED_INCREASE = "unexplained_increase"
    EXPLICIT_CONFLICT = "explicit_conflict"


class DepletionEstimationError(ConsumptionEstimationError):
    """Stocktake history cannot be converted into unambiguous depletion evidence."""


@dataclass(frozen=True, slots=True)
class StocktakeDepletionWindow:
    """One auditable interval between consecutive absolute inventory counts."""

    item: Item
    period_start: datetime
    period_end: datetime
    start_stocktake_id: str
    end_stocktake_id: str
    start_quantity: Quantity
    confirmed_purchases: Quantity
    end_quantity: Quantity
    explicit_consumption: Quantity
    purchase_event_ids: tuple[str, ...]
    explicit_observation_ids: tuple[str, ...]
    status: DepletionWindowStatus
    inferred_depletion: Quantity | None

    def __post_init__(self) -> None:
        start_id = self.start_stocktake_id.strip()
        end_id = self.end_stocktake_id.strip()
        if not start_id or not end_id or start_id == end_id:
            raise ValueError("depletion window requires two distinct stocktake ids")
        if self.period_start.tzinfo is None or self.period_start.utcoffset() is None:
            raise ValueError("depletion window period_start must be timezone-aware")
        if self.period_end.tzinfo is None or self.period_end.utcoffset() is None:
            raise ValueError("depletion window period_end must be timezone-aware")
        if self.period_end <= self.period_start:
            raise ValueError("depletion window period_end must be after period_start")
        quantities = (
            self.start_quantity,
            self.confirmed_purchases,
            self.end_quantity,
            self.explicit_consumption,
        )
        if not all(self.start_quantity.compatible_with(value) for value in quantities):
            raise ValueError("depletion window quantities must use one dimension")
        purchase_ids = tuple(self.purchase_event_ids)
        explicit_ids = tuple(self.explicit_observation_ids)
        if len(purchase_ids) != len(set(purchase_ids)):
            raise ValueError("depletion window contains duplicate purchase event ids")
        if len(explicit_ids) != len(set(explicit_ids)):
            raise ValueError("depletion window contains duplicate explicit observation ids")

        gross = add_decimals_exact(
            self.start_quantity.base_amount,
            self.confirmed_purchases.base_amount,
        )
        exact = subtract_decimals_exact(gross, self.end_quantity.base_amount)
        explicit = self.explicit_consumption.base_amount

        if exact < 0:
            expected_status = DepletionWindowStatus.UNEXPLAINED_INCREASE
            expected_depletion = None
        elif explicit > exact:
            expected_status = DepletionWindowStatus.EXPLICIT_CONFLICT
            expected_depletion = None
        elif exact == 0:
            expected_status = DepletionWindowStatus.ZERO_DEPLETION
            expected_depletion = Quantity(0, self.start_quantity.base_unit)
        else:
            expected_status = DepletionWindowStatus.USED
            expected_depletion = Quantity(exact, self.start_quantity.base_unit)

        if self.status is not expected_status:
            raise ValueError("depletion window status does not match exact stocktake arithmetic")
        if self.inferred_depletion != expected_depletion:
            raise ValueError(
                "depletion window inferred_depletion does not match exact stocktake arithmetic"
            )
        object.__setattr__(self, "start_stocktake_id", start_id)
        object.__setattr__(self, "end_stocktake_id", end_id)
        object.__setattr__(self, "purchase_event_ids", purchase_ids)
        object.__setattr__(self, "explicit_observation_ids", explicit_ids)

    @property
    def accepted_for_learning(self) -> bool:
        return self.status in {
            DepletionWindowStatus.USED,
            DepletionWindowStatus.ZERO_DEPLETION,
        }

    def usage_sample(self) -> UsageRateSample | None:
        if not self.accepted_for_learning or self.inferred_depletion is None:
            return None
        return UsageRateSample(
            evidence_id=f"stocktake:{self.start_stocktake_id}:{self.end_stocktake_id}",
            item=self.item,
            quantity=self.inferred_depletion,
            period_start=self.period_start,
            period_end=self.period_end,
        )


@dataclass(frozen=True, slots=True)
class DepletionLearningReport:
    item: Item
    windows: tuple[StocktakeDepletionWindow, ...]
    direct_observation_ids_used: tuple[str, ...]
    direct_observation_ids_shadowed: tuple[str, ...]
    estimate: ConsumptionEstimate | None

    def __post_init__(self) -> None:
        windows = tuple(self.windows)
        used = tuple(self.direct_observation_ids_used)
        shadowed = tuple(self.direct_observation_ids_shadowed)
        if len(used) != len(set(used)) or len(shadowed) != len(set(shadowed)):
            raise ValueError("depletion report observation ids must be unique")
        if set(used) & set(shadowed):
            raise ValueError("depletion report cannot both use and shadow one observation")
        if any(window.item != self.item for window in windows):
            raise ValueError("depletion report windows must belong to one Item")
        if self.estimate is not None and self.estimate.item != self.item:
            raise ValueError("depletion report estimate Item does not match report Item")
        object.__setattr__(self, "windows", windows)
        object.__setattr__(self, "direct_observation_ids_used", used)
        object.__setattr__(self, "direct_observation_ids_shadowed", shadowed)


def _visible_history(
    history: HouseholdHistory,
    *,
    as_of: datetime | None,
) -> HouseholdHistory:
    if as_of is None:
        return history
    return history.through(as_of)


def _same_item_identity(events, item_id: str) -> Item | None:
    item: Item | None = None
    for event in events:
        if event.item.id != item_id:
            continue
        if item is not None and event.item != item:
            raise DepletionEstimationError(
                f"conflicting depletion Item identity: {item_id}"
            )
        item = event.item
    return item


def _sum_quantities(
    quantities: tuple[Quantity, ...],
    *,
    base_unit: str,
) -> Quantity:
    total = Decimal(0)
    for quantity in quantities:
        base = quantity.as_base()
        if base.base_unit != base_unit:
            raise DepletionEstimationError("incompatible depletion evidence units")
        total = add_decimals_exact(total, base.base_amount)
    return Quantity(total, base_unit)


def derive_stocktake_depletion_windows(
    history: HouseholdHistory,
    item_id: str,
    *,
    as_of: datetime | None = None,
) -> tuple[StocktakeDepletionWindow, ...]:
    normalized = item_id.strip()
    if not normalized:
        raise ValueError("depletion item_id must not be empty")
    visible = _visible_history(history, as_of=as_of)
    item = _same_item_identity(visible.events, normalized)
    if item is None:
        return ()

    corrections = sorted(
        (
            event
            for event in visible.events
            if isinstance(event, InventoryCorrection) and event.item.id == normalized
        ),
        key=lambda event: (event.occurred_at, event.event_id.value),
    )
    for previous, current in zip(corrections, corrections[1:]):
        if previous.occurred_at == current.occurred_at:
            raise DepletionEstimationError(
                "multiple stocktakes for the same item and timestamp: "
                f"{normalized} at {current.occurred_at.isoformat()}"
            )

    windows: list[StocktakeDepletionWindow] = []
    for start, end in zip(corrections, corrections[1:]):
        if not start.quantity_on_hand.compatible_with(end.quantity_on_hand):
            raise DepletionEstimationError(
                f"incompatible stocktake units for item: {normalized}"
            )
        base_unit = start.quantity_on_hand.base_unit
        purchases = tuple(
            event
            for event in visible.events
            if isinstance(event, PurchaseEvent)
            and event.item.id == normalized
            and start.occurred_at < event.occurred_at <= end.occurred_at
        )
        explicit = tuple(
            event
            for event in visible.events
            if isinstance(event, ConsumptionObservation)
            and event.item.id == normalized
            and start.occurred_at <= event.period_start
            and event.period_end <= end.occurred_at
        )
        purchase_total = _sum_quantities(
            tuple(event.quantity for event in purchases),
            base_unit=base_unit,
        )
        explicit_total = _sum_quantities(
            tuple(event.quantity_consumed for event in explicit),
            base_unit=base_unit,
        )
        start_base = start.quantity_on_hand.as_base()
        end_base = end.quantity_on_hand.as_base()
        gross = add_decimals_exact(start_base.base_amount, purchase_total.base_amount)
        exact = subtract_decimals_exact(gross, end_base.base_amount)
        if exact < 0:
            status = DepletionWindowStatus.UNEXPLAINED_INCREASE
            inferred = None
        elif explicit_total.base_amount > exact:
            status = DepletionWindowStatus.EXPLICIT_CONFLICT
            inferred = None
        elif exact == 0:
            status = DepletionWindowStatus.ZERO_DEPLETION
            inferred = Quantity(0, base_unit)
        else:
            status = DepletionWindowStatus.USED
            inferred = Quantity(exact, base_unit)
        windows.append(
            StocktakeDepletionWindow(
                item=item,
                period_start=start.occurred_at,
                period_end=end.occurred_at,
                start_stocktake_id=start.event_id.value,
                end_stocktake_id=end.event_id.value,
                start_quantity=start_base,
                confirmed_purchases=purchase_total,
                end_quantity=end_base,
                explicit_consumption=explicit_total,
                purchase_event_ids=tuple(
                    event.event_id.value
                    for event in sorted(
                        purchases, key=lambda event: (event.occurred_at, event.event_id.value)
                    )
                ),
                explicit_observation_ids=tuple(
                    event.event_id.value
                    for event in sorted(
                        explicit,
                        key=lambda event: (
                            event.period_start,
                            event.period_end,
                            event.event_id.value,
                        ),
                    )
                ),
                status=status,
                inferred_depletion=inferred,
            )
        )
    return tuple(windows)


def _inside_accepted_window(
    observation: ConsumptionObservation,
    windows: tuple[StocktakeDepletionWindow, ...],
) -> bool:
    return any(
        window.accepted_for_learning
        and window.period_start <= observation.period_start
        and observation.period_end <= window.period_end
        for window in windows
    )


def depletion_learning_report(
    history: HouseholdHistory,
    item_id: str,
    *,
    as_of: datetime | None = None,
) -> DepletionLearningReport | None:
    normalized = item_id.strip()
    if not normalized:
        raise ValueError("depletion item_id must not be empty")
    visible = _visible_history(history, as_of=as_of)
    item = _same_item_identity(visible.events, normalized)
    if item is None:
        return None

    windows = derive_stocktake_depletion_windows(visible, normalized)
    direct = sorted(
        (
            event
            for event in visible.events
            if isinstance(event, ConsumptionObservation) and event.item.id == normalized
        ),
        key=lambda event: (event.period_start, event.period_end, event.event_id.value),
    )

    samples: list[UsageRateSample] = []
    for window in windows:
        sample = window.usage_sample()
        if sample is not None:
            samples.append(sample)

    direct_used: list[str] = []
    direct_shadowed: list[str] = []
    for observation in direct:
        if _inside_accepted_window(observation, windows):
            direct_shadowed.append(observation.event_id.value)
            continue
        direct_used.append(observation.event_id.value)
        samples.append(
            UsageRateSample(
                evidence_id=f"explicit:{observation.event_id.value}",
                item=observation.item,
                quantity=observation.quantity_consumed,
                period_start=observation.period_start,
                period_end=observation.period_end,
            )
        )

    estimate = estimate_usage_rate(tuple(samples))
    return DepletionLearningReport(
        item=item,
        windows=windows,
        direct_observation_ids_used=tuple(direct_used),
        direct_observation_ids_shadowed=tuple(direct_shadowed),
        estimate=estimate,
    )


def estimate_depletion(
    history: HouseholdHistory,
    item_id: str,
    *,
    as_of: datetime | None = None,
) -> ConsumptionEstimate | None:
    report = depletion_learning_report(history, item_id, as_of=as_of)
    return None if report is None else report.estimate


def depletion_learning_reports(
    history: HouseholdHistory,
    *,
    as_of: datetime | None = None,
) -> tuple[DepletionLearningReport, ...]:
    visible = _visible_history(history, as_of=as_of)
    item_ids = sorted({event.item.id for event in visible.events})
    reports: list[DepletionLearningReport] = []
    for item_id in item_ids:
        report = depletion_learning_report(visible, item_id)
        if report is not None:
            reports.append(report)
    return tuple(reports)


def estimate_all_depletion(
    history: HouseholdHistory,
    *,
    as_of: datetime | None = None,
) -> tuple[ConsumptionEstimate, ...]:
    return tuple(
        report.estimate
        for report in depletion_learning_reports(history, as_of=as_of)
        if report.estimate is not None
    )
