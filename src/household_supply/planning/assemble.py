from __future__ import annotations

from decimal import Decimal

from household_supply.domain import (
    Money,
    ObjectiveBreakdown,
    PlanStatus,
    PlanningProblem,
    ProcurementPlan,
    ProjectedLeftover,
    Quantity,
    RequirementCoverage,
)
from household_supply.domain._decimal import add_decimals_exact, subtract_decimals_exact

from .candidates import ItemCandidate
from .compile import CompiledRequirement


def assemble_feasible_plan(
    problem: PlanningProblem,
    requirements: tuple[CompiledRequirement, ...],
    selected: dict[str, ItemCandidate],
    *,
    minimum_required_cost: Money,
    objective_breakdown: ObjectiveBreakdown | None = None,
    explanation_prefix: tuple[str, ...] = (),
) -> ProcurementPlan:
    currency = problem.policy.budget.currency
    purchases = []
    coverage = []
    leftovers = []
    explanation = list(explanation_prefix)
    total_cost = Money.zero(currency)

    for requirement in requirements:
        candidate = selected[requirement.item_id]
        purchases.extend(candidate.purchases)
        total_cost = total_cost + candidate.purchase_cost

        covered_amount = add_decimals_exact(
            requirement.inventory_used.base_amount, candidate.purchased.base_amount
        )
        unused_inventory = subtract_decimals_exact(
            requirement.inventory_available.base_amount,
            requirement.inventory_used.base_amount,
        )
        overbuy = max(
            Decimal("0"),
            subtract_decimals_exact(covered_amount, requirement.required.base_amount),
        )
        leftover_amount = add_decimals_exact(unused_inventory, overbuy)

        coverage.append(
            RequirementCoverage(
                item_id=requirement.item_id,
                required=requirement.required,
                inventory_used=requirement.inventory_used,
                purchased=candidate.purchased,
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
            f"purchased {candidate.purchased.amount} {candidate.purchased.unit}"
        )

    return ProcurementPlan(
        status=PlanStatus.FEASIBLE,
        purchases=tuple(purchases),
        requirement_coverage=tuple(coverage),
        projected_leftovers=tuple(leftovers),
        total_cost=total_cost,
        budget_remaining=problem.policy.budget - total_cost,
        minimum_required_cost=minimum_required_cost,
        objective_breakdown=objective_breakdown,
        explanation=tuple(explanation),
    )
