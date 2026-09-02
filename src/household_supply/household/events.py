from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re

from household_supply.domain import Item, Quantity


_EVENT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _require_aware(value: datetime, *, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _normalize_optional_text(value: str) -> str:
    return value.strip()


@dataclass(frozen=True, slots=True, order=True)
class HouseholdEventId:
    value: str

    def __post_init__(self) -> None:
        value = self.value.strip()
        if not _EVENT_ID_PATTERN.fullmatch(value):
            raise ValueError(
                "household event id must contain 1-64 lowercase ASCII letters, "
                "digits, '_' or '-', and must start with a letter or digit"
            )
        object.__setattr__(self, "value", value)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class PurchaseEvent:
    """Observed completed purchase that increases household inventory.

    A procurement plan never creates this event automatically: planned purchase
    and completed purchase are distinct facts.
    """

    event_id: HouseholdEventId
    item: Item
    quantity: Quantity
    occurred_at: datetime
    recorded_at: datetime
    sku_id: str = ""
    source_ref: str = ""

    def __post_init__(self) -> None:
        if self.quantity.amount <= 0:
            raise ValueError("purchase quantity must be positive")
        _require_aware(self.occurred_at, label="purchase occurred_at")
        _require_aware(self.recorded_at, label="purchase recorded_at")
        if self.recorded_at < self.occurred_at:
            raise ValueError("purchase recorded_at must not precede occurred_at")
        object.__setattr__(self, "sku_id", _normalize_optional_text(self.sku_id))
        object.__setattr__(self, "source_ref", _normalize_optional_text(self.source_ref))

    @property
    def effective_at(self) -> datetime:
        return self.occurred_at


@dataclass(frozen=True, slots=True)
class InventoryCorrection:
    """Absolute on-hand count observed at a point in time.

    A correction supersedes the projected item balance at ``occurred_at`` but
    does not delete or rewrite earlier events.
    """

    event_id: HouseholdEventId
    item: Item
    quantity_on_hand: Quantity
    occurred_at: datetime
    recorded_at: datetime
    reason: str

    def __post_init__(self) -> None:
        _require_aware(self.occurred_at, label="inventory correction occurred_at")
        _require_aware(self.recorded_at, label="inventory correction recorded_at")
        if self.recorded_at < self.occurred_at:
            raise ValueError(
                "inventory correction recorded_at must not precede occurred_at"
            )
        reason = self.reason.strip()
        if not reason:
            raise ValueError("inventory correction reason must not be empty")
        object.__setattr__(self, "reason", reason)

    @property
    def effective_at(self) -> datetime:
        return self.occurred_at


@dataclass(frozen=True, slots=True)
class ConsumptionObservation:
    """Observed positive consumption across a bounded time interval."""

    event_id: HouseholdEventId
    item: Item
    quantity_consumed: Quantity
    period_start: datetime
    period_end: datetime
    recorded_at: datetime
    source_ref: str = ""

    def __post_init__(self) -> None:
        if self.quantity_consumed.amount <= 0:
            raise ValueError("consumption observation quantity must be positive")
        _require_aware(self.period_start, label="consumption period_start")
        _require_aware(self.period_end, label="consumption period_end")
        _require_aware(self.recorded_at, label="consumption recorded_at")
        if self.period_end <= self.period_start:
            raise ValueError("consumption period_end must be after period_start")
        if self.recorded_at < self.period_end:
            raise ValueError("consumption recorded_at must not precede period_end")
        object.__setattr__(self, "source_ref", _normalize_optional_text(self.source_ref))

    @property
    def effective_at(self) -> datetime:
        return self.period_end


HouseholdEvent = PurchaseEvent | InventoryCorrection | ConsumptionObservation


def event_kind(event: HouseholdEvent) -> str:
    if isinstance(event, PurchaseEvent):
        return "purchase"
    if isinstance(event, InventoryCorrection):
        return "inventory_correction"
    if isinstance(event, ConsumptionObservation):
        return "consumption_observation"
    raise TypeError(f"unsupported household event: {type(event)!r}")


def event_effective_at(event: HouseholdEvent) -> datetime:
    return event.effective_at
