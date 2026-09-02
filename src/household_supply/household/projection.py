from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from household_supply.domain import InventoryLot, InventorySnapshot, Item, Quantity
from household_supply.domain._decimal import add_decimals_exact, subtract_decimals_exact

from .events import (
    ConsumptionObservation,
    HouseholdEvent,
    InventoryCorrection,
    PurchaseEvent,
    event_effective_at,
)
from .history import HouseholdHistory


class HouseholdProjectionError(ValueError):
    """Household facts cannot be projected into one internally consistent state."""


@dataclass(frozen=True, slots=True)
class HouseholdBalance:
    item: Item
    quantity: Quantity

    def __post_init__(self) -> None:
        if self.quantity.amount < 0:  # guarded by Quantity, kept as an invariant
            raise ValueError("household balance must not be negative")


@dataclass(frozen=True, slots=True)
class HouseholdState:
    as_of: datetime
    balances: tuple[HouseholdBalance, ...]
    applied_event_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("household state as_of must be timezone-aware")
        balances = tuple(self.balances)
        item_ids = [balance.item.id for balance in balances]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("household state contains duplicate item balances")
        event_ids = tuple(self.applied_event_ids)
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("household state contains duplicate applied event ids")
        object.__setattr__(self, "balances", balances)
        object.__setattr__(self, "applied_event_ids", event_ids)

    def quantity_for(self, item_id: str) -> Quantity | None:
        normalized = item_id.strip()
        for balance in self.balances:
            if balance.item.id == normalized:
                return balance.quantity
        return None

    def inventory_snapshot(self) -> InventorySnapshot:
        return InventorySnapshot(
            tuple(
                InventoryLot(
                    id=f"household:{balance.item.id}",
                    item=balance.item,
                    quantity=balance.quantity,
                )
                for balance in self.balances
                if balance.quantity.amount > 0
            )
        )


def _eligible(event: HouseholdEvent, as_of: datetime) -> bool:
    return event.recorded_at <= as_of and event_effective_at(event) <= as_of


def _event_precedence(event: HouseholdEvent) -> int:
    # At an identical effective timestamp, a correction is the final observed
    # absolute count and therefore supersedes additive/subtractive facts there.
    if isinstance(event, PurchaseEvent):
        return 0
    if isinstance(event, ConsumptionObservation):
        return 1
    if isinstance(event, InventoryCorrection):
        return 2
    raise TypeError(f"unsupported household event: {type(event)!r}")


def _validate_item_identity(events: tuple[HouseholdEvent, ...]) -> dict[str, Item]:
    items: dict[str, Item] = {}
    for event in events:
        previous = items.get(event.item.id)
        if previous is not None and previous != event.item:
            raise HouseholdProjectionError(
                f"conflicting household Item identity: {event.item.id}"
            )
        items[event.item.id] = event.item
    return items


def _validate_consumption_intervals(events: tuple[HouseholdEvent, ...]) -> None:
    by_item: dict[str, list[ConsumptionObservation]] = {}
    for event in events:
        if isinstance(event, ConsumptionObservation):
            by_item.setdefault(event.item.id, []).append(event)
    for item_id, observations in by_item.items():
        observations.sort(
            key=lambda observation: (
                observation.period_start,
                observation.period_end,
                observation.event_id.value,
            )
        )
        previous: ConsumptionObservation | None = None
        for observation in observations:
            if previous is not None and observation.period_start < previous.period_end:
                raise HouseholdProjectionError(
                    "overlapping consumption observations for item "
                    f"{item_id}: {previous.event_id} and {observation.event_id}"
                )
            previous = observation


def _validate_corrections_do_not_split_consumption(
    events: tuple[HouseholdEvent, ...],
) -> None:
    corrections: dict[str, list[InventoryCorrection]] = {}
    observations: dict[str, list[ConsumptionObservation]] = {}
    for event in events:
        if isinstance(event, InventoryCorrection):
            corrections.setdefault(event.item.id, []).append(event)
        elif isinstance(event, ConsumptionObservation):
            observations.setdefault(event.item.id, []).append(event)
    for item_id, item_observations in observations.items():
        for observation in item_observations:
            for correction in corrections.get(item_id, ()):
                if observation.period_start < correction.occurred_at < observation.period_end:
                    raise HouseholdProjectionError(
                        "inventory correction splits a consumption observation interval for "
                        f"item {item_id}: {correction.event_id} inside {observation.event_id}"
                    )


def project_household_state(
    history: HouseholdHistory,
    *,
    as_of: datetime,
) -> HouseholdState:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("household projection as_of must be timezone-aware")

    events = tuple(event for event in history.events if _eligible(event, as_of))
    items = _validate_item_identity(events)
    _validate_consumption_intervals(events)
    _validate_corrections_do_not_split_consumption(events)

    correction_keys: set[tuple[str, datetime]] = set()
    for event in events:
        if isinstance(event, InventoryCorrection):
            key = (event.item.id, event.occurred_at)
            if key in correction_keys:
                raise HouseholdProjectionError(
                    "multiple inventory corrections for the same item and timestamp: "
                    f"{event.item.id} at {event.occurred_at.isoformat()}"
                )
            correction_keys.add(key)

    ordered = sorted(
        events,
        key=lambda event: (
            event_effective_at(event),
            _event_precedence(event),
            event.event_id.value,
        ),
    )
    balances: dict[str, Quantity] = {}
    applied: list[str] = []

    for event in ordered:
        item_id = event.item.id
        if isinstance(event, PurchaseEvent):
            base = event.quantity.as_base()
            existing = balances.get(item_id)
            if existing is None:
                balances[item_id] = base
            else:
                if not existing.compatible_with(base):
                    raise HouseholdProjectionError(
                        f"incompatible household units for item: {item_id}"
                    )
                balances[item_id] = Quantity(
                    add_decimals_exact(existing.base_amount, base.base_amount),
                    existing.base_unit,
                )

        elif isinstance(event, ConsumptionObservation):
            consumed = event.quantity_consumed.as_base()
            existing = balances.get(item_id)
            if existing is None:
                raise HouseholdProjectionError(
                    "consumption observation has no tracked inventory basis: "
                    f"{event.event_id}"
                )
            if not existing.compatible_with(consumed):
                raise HouseholdProjectionError(
                    f"incompatible household units for item: {item_id}"
                )
            remaining = subtract_decimals_exact(
                existing.base_amount, consumed.base_amount
            )
            if remaining < 0:
                raise HouseholdProjectionError(
                    "consumption exceeds tracked inventory before correction: "
                    f"{event.event_id}"
                )
            balances[item_id] = Quantity(remaining, existing.base_unit)

        elif isinstance(event, InventoryCorrection):
            corrected = event.quantity_on_hand.as_base()
            existing = balances.get(item_id)
            if existing is not None and not existing.compatible_with(corrected):
                raise HouseholdProjectionError(
                    f"incompatible household units for item: {item_id}"
                )
            balances[item_id] = corrected

        else:  # pragma: no cover - union guarded above
            raise TypeError(f"unsupported household event: {type(event)!r}")
        applied.append(event.event_id.value)

    result_balances = tuple(
        HouseholdBalance(item=items[item_id], quantity=quantity)
        for item_id, quantity in sorted(balances.items())
    )
    return HouseholdState(
        as_of=as_of,
        balances=result_balances,
        applied_event_ids=tuple(applied),
    )
