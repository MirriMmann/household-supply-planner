from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
from itertools import product

from household_supply.domain import (
    Demand,
    Money,
    PlanStatus,
    PlanningProblem,
    ProcurementPlan,
    ProjectedLeftover,
    Purchase,
    Quantity,
    RequirementCoverage,
)


@dataclass(frozen=True, slots=True)
class _Requirement:
    item_id: str
    required: Quantity
    inventory_available: Quantity
    inventory_used: Quantity
    net_required: Quantity


@dataclass(frozen=True, slots=True)
class _ItemSolution:
    purchases: tuple[Purchase, ...]
    purchased: Quantity
    cost: Money


_MAX_COMBINATIONS_PER_ITEM = 200_000


def _aggregate_demands(demands: tuple[Demand, ...]) -> dict[str, Quantity]:
    aggregated: dict[str, Quantity] = {}
    for demand in demands:
        base = demand.quantity.as_base()
        previous = aggregated.get(demand.item.id)
        if previous is None:
            aggregated[demand.item.id] = base
            continue
        if not previous.compatible_with(base):
            raise ValueError(
                f"incompatible demand units for item {demand.item.id}: "
                f"{previous.unit} and {base.unit}"
            )
        aggregated[demand.item.id] = Quantity(
            previous.base_amount + base.base_amount,
            previous.base_unit,
        )
    return aggregated


def _aggregate_inventory(problem: PlanningProblem) -> dict[str, Quantity]:
    aggregated: dict[str, Quantity] = {}
    for lot in problem.inventory.lots:
        base = lot.quantity.as_base()
        previous = aggregated.get(lot.item.id)
        if previous is None:
            aggregated[lot.item.id] = base
            continue
        if not previous.compatible_with(base):
            raise ValueError(
                f"incompatible inventory units for item {lot.item.id}: "
                f"{previous.unit} and {base.unit}"
            )
        aggregated[lot.item.id] = Quantity(
            previous.base_amount + base.base_amount,
            previous.base_unit,
        )
    return aggregated


def _requirements(problem: PlanningProblem) -> tuple[_Requirement, ...]:
    demands = _aggregate_demands(problem.demands)
    inventory = _aggregate_inventory(problem)
    result: list[_Requirement] = []

    for item_id in sorted(demands):
        required = demands[item_id]
        available = inventory.get(item_id, Quantity(0, required.base_unit))
        if not required.compatible_with(available):
            raise ValueError(
                f"inventory unit for item {item_id} is incompatible with demand"
            )
        available = available.as_base()
        used_amount = min(required.base_amount, available.base_amount)
        net_amount = required.base_amount - used_amount
        result.append(
            _Requirement(
                item_id=item_id,
                required=required,
                inventory_available=available,
                inventory_used=Quantity(used_amount, required.base_unit),
                net_required=Quantity(net_amount, required.base_unit),
            )
        )
    return tuple(result)


def _ceil_ratio(numerator: Decimal, denominator: Decimal) -> int:
    if denominator <= 0:
        raise ValueError("denominator must be positive")
    return int((numerator / denominator).to_integral_value(rounding=ROUND_CEILING))


def _solve_item(problem: PlanningProblem, requirement: _Requirement) -> _ItemSolution | None:
    currency = problem.policy.budget.currency
    compatible_offers = []
    for offer in sorted(problem.market.offers, key=lambda candidate: candidate.id):
        if not offer.available:
            continue
        if offer.price.currency != currency:
            continue
        if offer.sku.item.id != requirement.item_id:
            continue
        package = offer.sku.package_quantity.as_base()
        if not package.compatible_with(requirement.net_required):
            continue
        compatible_offers.append((offer, package))

    if requirement.net_required.amount == 0:
        return _ItemSolution(
            purchases=(),
            purchased=Quantity(0, requirement.required.base_unit),
            cost=Money.zero(currency),
        )

    if not compatible_offers:
        return None

    ranges: list[range] = []
    combinations = 1
    for _, package in compatible_offers:
        max_packs = _ceil_ratio(
            requirement.net_required.base_amount,
            package.base_amount,
        )
        candidate_range = range(max_packs + 1)
        ranges.append(candidate_range)
        combinations *= len(candidate_range)

    if combinations > _MAX_COMBINATIONS_PER_ITEM:
        raise RuntimeError(
            f"baseline search space for {requirement.item_id} is too large: "
            f"{combinations} combinations"
        )

    best_key: tuple[Decimal, Decimal, int, tuple[int, ...]] | None = None
    best_counts: tuple[int, ...] | None = None

    for counts in product(*ranges):
        if not any(counts):
            continue
        total_quantity = Decimal("0")
        total_cost = Decimal("0")
        total_packs = 0
        for count, (offer, package) in zip(counts, compatible_offers, strict=True):
            if count == 0:
                continue
            total_quantity += package.base_amount * count
            total_cost += offer.price.amount * count
            total_packs += count

        if total_quantity < requirement.net_required.base_amount:
            continue

        surplus = total_quantity - requirement.net_required.base_amount
        key = (total_cost, surplus, total_packs, counts)
        if best_key is None or key < best_key:
            best_key = key
            best_counts = counts

    if best_counts is None:
        return None

    purchases: list[Purchase] = []
    purchased_amount = Decimal("0")
    cost = Money.zero(currency)

    for count, (offer, package) in zip(best_counts, compatible_offers, strict=True):
        if count == 0:
            continue
        acquired = Quantity(package.base_amount * count, package.base_unit)
        purchase_cost = offer.price * count
        purchases.append(
            Purchase(
                offer=offer,
                packs=count,
                acquired_quantity=acquired,
                cost=purchase_cost,
            )
        )
        purchased_amount += acquired.base_amount
        cost = cost + purchase_cost

    return _ItemSolution(
        purchases=tuple(purchases),
        purchased=Quantity(purchased_amount, requirement.required.base_unit),
        cost=cost,
    )


def build_plan(problem: PlanningProblem) -> ProcurementPlan:
    currency = problem.policy.budget.currency
    requirements = _requirements(problem)
    item_solutions: dict[str, _ItemSolution] = {}
    unavailable: list[str] = []

    for requirement in requirements:
        solution = _solve_item(problem, requirement)
        if solution is None:
            unavailable.append(requirement.item_id)
        else:
            item_solutions[requirement.item_id] = solution

    if unavailable:
        reasons = tuple(
            f"no available compatible offer can cover required item: {item_id}"
            for item_id in unavailable
        )
        return ProcurementPlan(
            status=PlanStatus.INFEASIBLE,
            purchases=(),
            requirement_coverage=(),
            projected_leftovers=(),
            total_cost=Money.zero(currency),
            budget_remaining=problem.policy.budget,
            minimum_required_cost=None,
            infeasibility_reasons=reasons,
            explanation=("planning stopped because required market coverage is missing",),
        )

    minimum_required_cost = Money.zero(currency)
    for solution in item_solutions.values():
        minimum_required_cost = minimum_required_cost + solution.cost

    if minimum_required_cost.amount > problem.policy.budget.amount:
        shortage = minimum_required_cost.amount - problem.policy.budget.amount
        return ProcurementPlan(
            status=PlanStatus.INFEASIBLE,
            purchases=(),
            requirement_coverage=(),
            projected_leftovers=(),
            total_cost=Money.zero(currency),
            budget_remaining=problem.policy.budget,
            minimum_required_cost=minimum_required_cost,
            infeasibility_reasons=(
                f"minimum required purchase cost exceeds budget by {shortage} {currency}",
            ),
            explanation=(
                f"minimum package-aware cost is {minimum_required_cost.amount} {currency}, "
                f"budget is {problem.policy.budget.amount} {currency}",
            ),
        )

    purchases: list[Purchase] = []
    coverage: list[RequirementCoverage] = []
    leftovers: list[ProjectedLeftover] = []
    explanation: list[str] = []

    for requirement in requirements:
        solution = item_solutions[requirement.item_id]
        purchases.extend(solution.purchases)
        covered_amount = requirement.inventory_used.base_amount + solution.purchased.base_amount
        leftover_amount = (
            requirement.inventory_available.base_amount
            - requirement.inventory_used.base_amount
            + max(Decimal("0"), covered_amount - requirement.required.base_amount)
        )
        coverage.append(
            RequirementCoverage(
                item_id=requirement.item_id,
                required=requirement.required,
                inventory_used=requirement.inventory_used,
                purchased=solution.purchased,
                covered=Quantity(
                    min(covered_amount, requirement.required.base_amount),
                    requirement.required.base_unit,
                ),
            )
        )
        leftovers.append(
            ProjectedLeftover(
                item_id=requirement.item_id,
                quantity=Quantity(leftover_amount, requirement.required.base_unit),
            )
        )
        explanation.append(
            f"{requirement.item_id}: required {requirement.required.amount} "
            f"{requirement.required.unit}, inventory supplied "
            f"{requirement.inventory_used.amount} {requirement.inventory_used.unit}, "
            f"purchased {solution.purchased.amount} {solution.purchased.unit}"
        )

    plan = ProcurementPlan(
        status=PlanStatus.FEASIBLE,
        purchases=tuple(purchases),
        requirement_coverage=tuple(coverage),
        projected_leftovers=tuple(leftovers),
        total_cost=minimum_required_cost,
        budget_remaining=problem.policy.budget - minimum_required_cost,
        minimum_required_cost=minimum_required_cost,
        explanation=tuple(explanation),
    )

    from .validate import validate_plan

    validate_plan(problem, plan)
    return plan
