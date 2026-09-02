from __future__ import annotations

from dataclasses import dataclass

from household_supply.domain import (
    CatalogSnapshot,
    InventoryLot,
    InventorySnapshot,
    Item,
    Money,
    MultiObjectivePolicy,
    PlanningPolicy,
    PlanningProblem,
    ProcurementPlan,
    Quantity,
    SKU,
)
from household_supply.market import MarketCompilation
from household_supply.planning import validate_multi_objective_plan, validate_plan


class ApplicationRequestError(ValueError):
    """The application request cannot be mapped to the configured domain surface."""


class UnknownCatalogItemError(ApplicationRequestError):
    """A request references an Item that is not present in the configured catalog."""


@dataclass(frozen=True, slots=True)
class RequestedItem:
    item_id: str
    quantity: Quantity

    def __post_init__(self) -> None:
        item_id = self.item_id.strip()
        if not item_id:
            raise ApplicationRequestError("requested item_id must not be empty")
        if self.quantity.amount <= 0:
            raise ApplicationRequestError("requested quantity must be positive")
        object.__setattr__(self, "item_id", item_id)


@dataclass(frozen=True, slots=True)
class InventoryInput:
    lot_id: str
    item_id: str
    quantity: Quantity

    def __post_init__(self) -> None:
        lot_id = self.lot_id.strip()
        item_id = self.item_id.strip()
        if not lot_id:
            raise ApplicationRequestError("inventory lot_id must not be empty")
        if not item_id:
            raise ApplicationRequestError("inventory item_id must not be empty")
        if self.quantity.amount <= 0:
            raise ApplicationRequestError("inventory quantity must be positive")
        object.__setattr__(self, "lot_id", lot_id)
        object.__setattr__(self, "item_id", item_id)


@dataclass(frozen=True, slots=True)
class ApplicationPlanRequest:
    demands: tuple[RequestedItem, ...]
    budget: Money
    inventory: tuple[InventoryInput, ...] = ()
    objective_policy: MultiObjectivePolicy | None = None

    def __post_init__(self) -> None:
        demands = tuple(self.demands)
        inventory = tuple(self.inventory)
        if not demands:
            raise ApplicationRequestError("application request requires at least one demand")

        demand_ids = [demand.item_id for demand in demands]
        if len(demand_ids) != len(set(demand_ids)):
            raise ApplicationRequestError(
                "application request contains duplicate demand item_id values"
            )

        lot_ids = [entry.lot_id for entry in inventory]
        if len(lot_ids) != len(set(lot_ids)):
            raise ApplicationRequestError(
                "application request contains duplicate inventory lot_id values"
            )

        if self.budget.amount < 0:
            raise ApplicationRequestError("application budget must not be negative")
        if (
            self.objective_policy is not None
            and self.objective_policy.additional_store_penalty.currency
            != self.budget.currency
        ):
            raise ApplicationRequestError(
                "objective policy currency must match application budget currency"
            )
        if self.objective_policy is not None:
            dead_penalties = {
                penalty.item_id for penalty in self.objective_policy.surplus_penalties
            } - set(demand_ids)
            if dead_penalties:
                raise ApplicationRequestError(
                    "surplus penalty references item not present in request demands: "
                    + ", ".join(sorted(dead_penalties))
                )

        object.__setattr__(self, "demands", demands)
        object.__setattr__(self, "inventory", inventory)

    def effective_objective_policy(self) -> MultiObjectivePolicy:
        return self.objective_policy or MultiObjectivePolicy.zero(self.budget.currency)


def catalog_items_by_id(catalog: CatalogSnapshot) -> dict[str, Item]:
    items: dict[str, Item] = {}
    for sku in catalog.skus:
        existing = items.get(sku.item.id)
        if existing is not None and existing != sku.item:
            raise ValueError(f"catalog contains conflicting Item identity: {sku.item.id}")
        items[sku.item.id] = sku.item
    return items


def validate_application_request_catalog(
    request: ApplicationPlanRequest, catalog: CatalogSnapshot
) -> None:
    items = catalog_items_by_id(catalog)
    skus_by_item: dict[str, list[SKU]] = {}
    for sku in catalog.skus:
        skus_by_item.setdefault(sku.item.id, []).append(sku)

    for requested in request.demands:
        if requested.item_id not in items:
            raise UnknownCatalogItemError(
                f"requested item is not present in configured catalog: {requested.item_id}"
            )
        skus = skus_by_item[requested.item_id]
        if not any(requested.quantity.compatible_with(sku.package_quantity) for sku in skus):
            raise ApplicationRequestError(
                f"requested quantity unit is incompatible with catalog item: {requested.item_id}"
            )

    demand_by_item = {demand.item_id: demand for demand in request.demands}
    for entry in request.inventory:
        if entry.item_id not in items:
            raise UnknownCatalogItemError(
                f"inventory item is not present in configured catalog: {entry.item_id}"
            )
        skus = skus_by_item[entry.item_id]
        if not any(entry.quantity.compatible_with(sku.package_quantity) for sku in skus):
            raise ApplicationRequestError(
                f"inventory quantity unit is incompatible with catalog item: {entry.item_id}"
            )
        requested = demand_by_item.get(entry.item_id)
        if requested is not None and not entry.quantity.compatible_with(requested.quantity):
            raise ApplicationRequestError(
                f"inventory quantity is incompatible with requested demand: {entry.item_id}"
            )


def build_application_problem(
    request: ApplicationPlanRequest,
    compilation: MarketCompilation,
) -> PlanningProblem:
    from household_supply.domain import Demand

    items = catalog_items_by_id(compilation.catalog)

    demands = []
    for requested in request.demands:
        item = items.get(requested.item_id)
        if item is None:
            raise UnknownCatalogItemError(
                f"requested item is not present in configured catalog: {requested.item_id}"
            )
        demands.append(Demand(item, requested.quantity, "application request"))

    lots = []
    for entry in request.inventory:
        item = items.get(entry.item_id)
        if item is None:
            raise UnknownCatalogItemError(
                f"inventory item is not present in configured catalog: {entry.item_id}"
            )
        lots.append(
            InventoryLot(
                id=entry.lot_id,
                item=item,
                quantity=entry.quantity,
            )
        )

    return PlanningProblem(
        demands=tuple(demands),
        inventory=InventorySnapshot(tuple(lots)),
        market=compilation.snapshot,
        policy=PlanningPolicy(request.budget),
    )


@dataclass(frozen=True, slots=True)
class ApplicationPlanResult:
    """Self-validating record for one complete application planning request."""

    request: ApplicationPlanRequest
    market_compilation: MarketCompilation
    problem: PlanningProblem
    objective_policy: MultiObjectivePolicy
    plan: ProcurementPlan

    def __post_init__(self) -> None:
        expected_problem = build_application_problem(self.request, self.market_compilation)
        if self.problem != expected_problem:
            raise ValueError(
                "application result planning problem does not match request/market basis"
            )
        if self.objective_policy != self.request.effective_objective_policy():
            raise ValueError(
                "application result objective policy does not match request policy"
            )
        validate_plan(self.problem, self.plan)
        validate_multi_objective_plan(self.problem, self.objective_policy, self.plan)
