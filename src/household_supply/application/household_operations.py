from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from household_supply.domain import CatalogSnapshot, Quantity, SKU
from household_supply.domain._decimal import multiply_decimal_by_int_exact
from household_supply.household import (
    DepletionLearningReport,
    HouseholdEvent,
    HouseholdEventId,
    HouseholdEventRepositoryError,
    HouseholdHistory,
    HouseholdLearningService,
    HouseholdState,
    InventoryCorrection,
    PurchaseEvent,
    depletion_learning_reports,
    project_household_state,
)

from .models import ApplicationRequestError, UnknownCatalogItemError, catalog_items_by_id
from .persistence import PlanId, PlanRecord, PlanRepository


OperationsClock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _require_aware(value: datetime, *, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


class HouseholdOperationError(ApplicationRequestError):
    """A household mutation request is invalid for the configured household."""


class HouseholdOperationConflictError(HouseholdOperationError):
    """A requested event identity conflicts with an already recorded fact."""


class HouseholdPlanNotFoundError(HouseholdOperationError):
    """A plan-linked purchase refers to a missing historical plan."""


@dataclass(frozen=True, slots=True)
class StocktakeCommand:
    event_id: HouseholdEventId
    item_id: str
    quantity: Quantity
    occurred_at: datetime | None = None
    reason: str = "stocktake"

    def __post_init__(self) -> None:
        item_id = self.item_id.strip()
        reason = self.reason.strip()
        if not item_id:
            raise HouseholdOperationError("stocktake item_id must not be empty")
        if not reason:
            raise HouseholdOperationError("stocktake reason must not be empty")
        if self.occurred_at is not None:
            _require_aware(self.occurred_at, label="stocktake occurred_at")
        object.__setattr__(self, "item_id", item_id)
        object.__setattr__(self, "reason", reason)


@dataclass(frozen=True, slots=True)
class PurchaseConfirmationCommand:
    event_id: HouseholdEventId
    sku_id: str
    packs: int
    occurred_at: datetime | None = None

    def __post_init__(self) -> None:
        sku_id = self.sku_id.strip()
        if not sku_id:
            raise HouseholdOperationError("purchase confirmation sku_id must not be empty")
        if type(self.packs) is not int or not 1 <= self.packs <= 100_000:
            raise HouseholdOperationError(
                "purchase confirmation packs must be an integer from 1 to 100000"
            )
        if self.occurred_at is not None:
            _require_aware(self.occurred_at, label="purchase occurred_at")
        object.__setattr__(self, "sku_id", sku_id)


@dataclass(frozen=True, slots=True)
class PurchaseConfirmationResult:
    event: PurchaseEvent
    actual_packs: int
    plan_id: PlanId | None
    planned_packs: int | None

    def __post_init__(self) -> None:
        if type(self.actual_packs) is not int or self.actual_packs <= 0:
            raise ValueError("purchase confirmation actual_packs must be positive")
        if self.plan_id is None and self.planned_packs is not None:
            raise ValueError("manual purchase cannot have planned_packs")
        if self.plan_id is not None and (
            type(self.planned_packs) is not int or self.planned_packs < 0
        ):
            raise ValueError("plan-linked purchase planned_packs must be non-negative")


def _same_quantity(left: Quantity, right: Quantity) -> bool:
    return left.compatible_with(right) and left.base_amount == right.base_amount


@dataclass(frozen=True, slots=True)
class HouseholdOperationsService:
    """Single-event household mutation boundary for a closed replenishment loop."""

    household: HouseholdLearningService
    catalog: CatalogSnapshot
    plans: PlanRepository
    clock: OperationsClock = _utc_now

    def __post_init__(self) -> None:
        if not callable(self.clock):
            raise TypeError("household operations clock must be callable")

    def _now(self) -> datetime:
        now = self.clock()
        _require_aware(now, label="household operations clock")
        return now

    def _sku(self, sku_id: str) -> SKU:
        matches = [sku for sku in self.catalog.skus if sku.id == sku_id]
        if not matches:
            raise UnknownCatalogItemError(
                f"purchase SKU is not present in configured catalog: {sku_id}"
            )
        return matches[0]

    def _item(self, item_id: str):
        item = catalog_items_by_id(self.catalog).get(item_id)
        if item is None:
            raise UnknownCatalogItemError(
                f"stocktake item is not present in configured catalog: {item_id}"
            )
        dimensions = {
            sku.package_quantity.dimension
            for sku in self.catalog.skus
            if sku.item.id == item_id
        }
        return item, dimensions

    def _preflight_event(self, event: HouseholdEvent, *, as_of: datetime) -> None:
        history = self.household.history()
        candidate = HouseholdHistory(history.events + (event,))
        project_household_state(candidate, as_of=as_of)
        # Depletion derivation may reject same-time stocktakes or conflicting
        # identities before we make the append durable.
        depletion_learning_reports(candidate, as_of=as_of)

    def _existing_stocktake(
        self,
        command: StocktakeCommand,
        *,
        item,
    ) -> InventoryCorrection | None:
        existing = self.household.repository.get(command.event_id)
        if existing is None:
            return None
        if not isinstance(existing, InventoryCorrection):
            raise HouseholdOperationConflictError(
                f"household event id already belongs to another fact: {command.event_id}"
            )
        if existing.item != item or not _same_quantity(existing.quantity_on_hand, command.quantity):
            raise HouseholdOperationConflictError(
                f"stocktake event id conflicts with existing fact: {command.event_id}"
            )
        if existing.reason != command.reason:
            raise HouseholdOperationConflictError(
                f"stocktake event id conflicts with existing reason: {command.event_id}"
            )
        if command.occurred_at is not None and existing.occurred_at != command.occurred_at:
            raise HouseholdOperationConflictError(
                f"stocktake event id conflicts with existing occurred_at: {command.event_id}"
            )
        return existing

    def record_stocktake(self, command: StocktakeCommand) -> InventoryCorrection:
        item, dimensions = self._item(command.item_id)
        if command.quantity.dimension not in dimensions:
            raise HouseholdOperationError(
                "stocktake quantity dimension is incompatible with configured item packages: "
                f"{command.item_id}"
            )
        existing = self._existing_stocktake(command, item=item)
        if existing is not None:
            return existing

        recorded_at = self._now()
        occurred_at = command.occurred_at or recorded_at
        try:
            event = InventoryCorrection(
                event_id=command.event_id,
                item=item,
                quantity_on_hand=command.quantity,
                occurred_at=occurred_at,
                recorded_at=recorded_at,
                reason=command.reason,
            )
        except ValueError as exc:
            raise HouseholdOperationError(str(exc)) from exc
        try:
            self._preflight_event(event, as_of=recorded_at)
            self.household.record(event)
        except (HouseholdEventRepositoryError, ValueError):
            raced = self._existing_stocktake(command, item=item)
            if raced is not None:
                return raced
            raise
        return event

    def _planned_packs(self, record: PlanRecord, sku: SKU) -> int:
        result = record.result.to_mapping()
        purchases = result.get("purchases")
        if not isinstance(purchases, list):
            raise RuntimeError("stored plan purchases invariant violated")
        total = 0
        for purchase in purchases:
            if not isinstance(purchase, dict):
                raise RuntimeError("stored plan purchase invariant violated")
            if purchase.get("sku_id") != sku.id:
                continue
            if purchase.get("item_id") != sku.item.id:
                raise HouseholdOperationConflictError(
                    "historical plan SKU identity conflicts with configured catalog: "
                    f"{sku.id}"
                )
            packs = purchase.get("packs")
            if type(packs) is not int or packs < 0:
                raise RuntimeError("stored plan purchase pack count invariant violated")
            acquired = purchase.get("acquired_quantity")
            if not isinstance(acquired, dict) or set(acquired) != {"amount", "unit"}:
                raise RuntimeError("stored plan acquired quantity invariant violated")
            try:
                acquired_quantity = Quantity(acquired["amount"], acquired["unit"])
            except (TypeError, ValueError) as exc:
                raise RuntimeError("stored plan acquired quantity invariant violated") from exc
            expected_amount = multiply_decimal_by_int_exact(
                sku.package_quantity.amount, packs
            )
            expected_quantity = Quantity(expected_amount, sku.package_quantity.unit)
            if not _same_quantity(acquired_quantity, expected_quantity):
                raise HouseholdOperationConflictError(
                    "historical plan SKU package conflicts with configured catalog: "
                    f"{sku.id}"
                )
            total += packs
        return total

    def _existing_purchase(
        self,
        command: PurchaseConfirmationCommand,
        *,
        sku: SKU,
        quantity: Quantity,
        source_ref: str,
    ) -> PurchaseEvent | None:
        existing = self.household.repository.get(command.event_id)
        if existing is None:
            return None
        if not isinstance(existing, PurchaseEvent):
            raise HouseholdOperationConflictError(
                f"household event id already belongs to another fact: {command.event_id}"
            )
        if (
            existing.item != sku.item
            or existing.sku_id != sku.id
            or not _same_quantity(existing.quantity, quantity)
            or existing.source_ref != source_ref
        ):
            raise HouseholdOperationConflictError(
                f"purchase event id conflicts with existing fact: {command.event_id}"
            )
        if command.occurred_at is not None and existing.occurred_at != command.occurred_at:
            raise HouseholdOperationConflictError(
                f"purchase event id conflicts with existing occurred_at: {command.event_id}"
            )
        return existing

    def record_purchase(
        self,
        command: PurchaseConfirmationCommand,
        *,
        plan_id: PlanId | None = None,
    ) -> PurchaseConfirmationResult:
        sku = self._sku(command.sku_id)
        amount = multiply_decimal_by_int_exact(sku.package_quantity.amount, command.packs)
        quantity = Quantity(amount, sku.package_quantity.unit)

        planned_packs: int | None = None
        source_ref = "manual"
        if plan_id is not None:
            record = self.plans.get(plan_id)
            if record is None:
                raise HouseholdPlanNotFoundError(f"plan not found: {plan_id}")
            planned_packs = self._planned_packs(record, sku)
            source_ref = f"plan:{plan_id.value}"

        existing = self._existing_purchase(
            command,
            sku=sku,
            quantity=quantity,
            source_ref=source_ref,
        )
        if existing is not None:
            return PurchaseConfirmationResult(
                event=existing,
                actual_packs=command.packs,
                plan_id=plan_id,
                planned_packs=planned_packs,
            )

        recorded_at = self._now()
        occurred_at = command.occurred_at or recorded_at
        try:
            event = PurchaseEvent(
                event_id=command.event_id,
                item=sku.item,
                quantity=quantity,
                occurred_at=occurred_at,
                recorded_at=recorded_at,
                sku_id=sku.id,
                source_ref=source_ref,
            )
        except ValueError as exc:
            raise HouseholdOperationError(str(exc)) from exc
        try:
            self._preflight_event(event, as_of=recorded_at)
            self.household.record(event)
        except (HouseholdEventRepositoryError, ValueError):
            raced = self._existing_purchase(
                command,
                sku=sku,
                quantity=quantity,
                source_ref=source_ref,
            )
            if raced is None:
                raise
            event = raced
        return PurchaseConfirmationResult(
            event=event,
            actual_packs=command.packs,
            plan_id=plan_id,
            planned_packs=planned_packs,
        )

    def history(self) -> HouseholdHistory:
        return self.household.history()

    def state(self, *, as_of: datetime | None = None) -> HouseholdState:
        resolved = as_of or self._now()
        _require_aware(resolved, label="household state as_of")
        return project_household_state(self.history(), as_of=resolved)

    def depletion_reports(
        self, *, as_of: datetime | None = None
    ) -> tuple[DepletionLearningReport, ...]:
        resolved = as_of or self._now()
        _require_aware(resolved, label="household depletion as_of")
        return depletion_learning_reports(self.history(), as_of=resolved)
