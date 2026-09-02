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
