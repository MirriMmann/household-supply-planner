from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .demand import Demand
from .inventory import InventorySnapshot
from .market import MarketSnapshot, Offer
from .money import Money
from .quantity import Quantity


@dataclass(frozen=True, slots=True)
class PlanningPolicy:
    budget: Money

    def __post_init__(self) -> None:
        if self.budget.amount < 0:
            raise ValueError("budget must not be negative")


@dataclass(frozen=True, slots=True)
class PlanningProblem:
    demands: tuple[Demand, ...]
    inventory: InventorySnapshot
    market: MarketSnapshot
    policy: PlanningPolicy

    def __post_init__(self) -> None:
        object.__setattr__(self, "demands", tuple(self.demands))


class PlanStatus(StrEnum):
    FEASIBLE = "feasible"
    INFEASIBLE = "infeasible"


@dataclass(frozen=True, slots=True)
class Purchase:
    offer: Offer
    packs: int
    acquired_quantity: Quantity
    cost: Money

    def __post_init__(self) -> None:
        if self.packs <= 0:
            raise ValueError("purchase packs must be positive")


@dataclass(frozen=True, slots=True)
class RequirementCoverage:
    item_id: str
    required: Quantity
    inventory_used: Quantity
    purchased: Quantity
    covered: Quantity


@dataclass(frozen=True, slots=True)
class ProjectedLeftover:
    item_id: str
    quantity: Quantity


@dataclass(frozen=True, slots=True)
class ObjectiveBreakdown:
    purchase_cost: Money
    surplus_penalty: Money
    additional_store_penalty: Money
    total_score: Money
    selected_sellers: tuple[str, ...]
    additional_store_count: int

    def __post_init__(self) -> None:
        currency = self.purchase_cost.currency
        for value in (
            self.surplus_penalty,
            self.additional_store_penalty,
            self.total_score,
        ):
            if value.currency != currency:
                raise ValueError("objective breakdown currencies must match")
        if self.additional_store_count < 0:
            raise ValueError("additional_store_count must not be negative")
        if tuple(sorted(set(self.selected_sellers))) != self.selected_sellers:
            raise ValueError("selected_sellers must be unique and sorted")
        expected_store_count = max(len(self.selected_sellers) - 1, 0)
        if self.additional_store_count != expected_store_count:
            raise ValueError("additional_store_count does not match selected_sellers")
        expected_total = (
            self.purchase_cost
            + self.surplus_penalty
            + self.additional_store_penalty
        )
        if self.total_score != expected_total:
            raise ValueError("objective total_score does not match objective terms")


@dataclass(frozen=True, slots=True)
class ProcurementPlan:
    status: PlanStatus
    purchases: tuple[Purchase, ...]
    requirement_coverage: tuple[RequirementCoverage, ...]
    projected_leftovers: tuple[ProjectedLeftover, ...]
    total_cost: Money
    budget_remaining: Money
    minimum_required_cost: Money | None = None
    infeasibility_reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    explanation: tuple[str, ...] = ()
    objective_breakdown: ObjectiveBreakdown | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "purchases", tuple(self.purchases))
        object.__setattr__(
            self, "requirement_coverage", tuple(self.requirement_coverage)
        )
        object.__setattr__(
            self, "projected_leftovers", tuple(self.projected_leftovers)
        )
        object.__setattr__(
            self, "infeasibility_reasons", tuple(self.infeasibility_reasons)
        )
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(self, "explanation", tuple(self.explanation))
