from __future__ import annotations

from decimal import Decimal

from household_supply.domain import PlanStatus, PlanningProblem, ProcurementPlan, Quantity
from household_supply.domain._decimal import (
    add_decimals_exact,
    multiply_decimal_by_int_exact,
    subtract_decimals_exact,
)

from .compile import compile_requirements


class PlanValidationError(ValueError):
    """Raised when a feasible procurement plan violates M1 invariants."""


def _unique_by_item(entries, *, label: str):
    result = {}
    for entry in entries:
        if entry.item_id in result:
            raise PlanValidationError(f"duplicate {label} entry: {entry.item_id}")
        result[entry.item_id] = entry
    return result


def validate_plan(problem: PlanningProblem, plan: ProcurementPlan) -> None:
    if plan.status is not PlanStatus.FEASIBLE:
        return

    budget = problem.policy.budget
    if plan.total_cost.currency != budget.currency:
        raise PlanValidationError("plan total currency differs from budget currency")
    if plan.budget_remaining.currency != budget.currency:
        raise PlanValidationError("plan remaining-budget currency differs from budget currency")
    if plan.total_cost.amount > budget.amount:
        raise PlanValidationError("feasible plan exceeds hard budget")
    if plan.budget_remaining != budget - plan.total_cost:
        raise PlanValidationError("plan remaining budget does not match budget minus total cost")

    requirements = compile_requirements(problem)
    requirements_by_item = {entry.item_id: entry for entry in requirements}
    expected_item_ids = set(requirements_by_item)

    market_offers = tuple(problem.market.offers)
    expected_cost = Decimal("0")
    purchased_by_item: dict[str, Decimal] = {}

    for purchase in plan.purchases:
        if purchase.packs <= 0:
            raise PlanValidationError("purchase contains non-positive package count")
        if purchase.offer not in market_offers:
            raise PlanValidationError(
                f"purchase uses offer outside market snapshot: {purchase.offer.id}"
            )
        if not purchase.offer.available:
            raise PlanValidationError("purchase uses unavailable offer")
        if purchase.offer.price.currency != budget.currency:
            raise PlanValidationError("purchase currency differs from budget currency")

        item_id = purchase.offer.sku.item.id
        if item_id not in expected_item_ids:
            raise PlanValidationError(f"purchase is unrelated to any requirement: {item_id}")

        expected_purchase_cost = purchase.offer.price * purchase.packs
        if purchase.cost != expected_purchase_cost:
            raise PlanValidationError(
                f"purchase cost does not match offer price and package count: {purchase.offer.id}"
            )
        expected_cost = add_decimals_exact(
            expected_cost, expected_purchase_cost.amount
        )

        expected_quantity = multiply_decimal_by_int_exact(
            purchase.offer.sku.package_quantity.as_base().base_amount,
            purchase.packs,
        )
        acquired = purchase.acquired_quantity.as_base()
        requirement = requirements_by_item[item_id]
        if not acquired.compatible_with(requirement.required):
            raise PlanValidationError(f"purchase quantity has incompatible unit: {item_id}")
        if acquired.base_amount != expected_quantity:
            raise PlanValidationError("purchase quantity does not match package count")
        purchased_by_item[item_id] = add_decimals_exact(
            purchased_by_item.get(item_id, Decimal("0")), expected_quantity
        )

    if expected_cost != plan.total_cost.amount:
        raise PlanValidationError("plan total cost does not match purchases")

    coverage_by_item = _unique_by_item(
        plan.requirement_coverage, label="requirement coverage"
    )
    if set(coverage_by_item) != expected_item_ids:
        missing = sorted(expected_item_ids - set(coverage_by_item))
        extra = sorted(set(coverage_by_item) - expected_item_ids)
        raise PlanValidationError(
            f"requirement coverage does not match problem; missing={missing}, extra={extra}"
        )

    leftovers_by_item = _unique_by_item(
        plan.projected_leftovers, label="projected leftover"
    )
    if set(leftovers_by_item) != expected_item_ids:
        missing = sorted(expected_item_ids - set(leftovers_by_item))
        extra = sorted(set(leftovers_by_item) - expected_item_ids)
        raise PlanValidationError(
            f"projected leftovers do not match problem; missing={missing}, extra={extra}"
        )

    for item_id, requirement in requirements_by_item.items():
        coverage = coverage_by_item[item_id]
        if coverage.required != requirement.required:
            raise PlanValidationError(f"coverage required quantity mismatch: {item_id}")
        if coverage.inventory_used != requirement.inventory_used:
            raise PlanValidationError(f"coverage inventory usage mismatch: {item_id}")

        purchased_amount = purchased_by_item.get(item_id, Decimal("0"))
        expected_purchased = Quantity(purchased_amount, requirement.required.base_unit)
        if coverage.purchased != expected_purchased:
            raise PlanValidationError(f"coverage purchase mismatch: {item_id}")

        supplied_amount = add_decimals_exact(
            requirement.inventory_used.base_amount, purchased_amount
        )
        expected_covered = Quantity(
            min(supplied_amount, requirement.required.base_amount),
            requirement.required.base_unit,
        )
        if coverage.covered != expected_covered:
            raise PlanValidationError(f"coverage covered quantity mismatch: {item_id}")
        if coverage.covered.base_amount < requirement.required.base_amount:
            raise PlanValidationError(f"requirement is under-covered: {item_id}")

        unused_inventory = subtract_decimals_exact(
            requirement.inventory_available.base_amount,
            requirement.inventory_used.base_amount,
        )
        overbuy = max(
            Decimal("0"),
            subtract_decimals_exact(
                supplied_amount, requirement.required.base_amount
            ),
        )
        expected_leftover_amount = add_decimals_exact(unused_inventory, overbuy)
        expected_leftover = Quantity(
            expected_leftover_amount, requirement.required.base_unit
        )
        if leftovers_by_item[item_id].quantity != expected_leftover:
            raise PlanValidationError(f"projected leftover mismatch: {item_id}")


def validate_multi_objective_plan(
    problem: PlanningProblem,
    policy,
    plan: ProcurementPlan,
) -> None:
    """Validate M3 objective accounting independently of candidate selection."""

    from household_supply.domain import MultiObjectivePolicy
    from .baseline import build_plan
    from .objective import objective_breakdown_for_plan, validate_objective_policy_currency

    if not isinstance(policy, MultiObjectivePolicy):
        raise TypeError("policy must be MultiObjectivePolicy")

    validate_objective_policy_currency(problem, policy)

    if plan.status is not PlanStatus.FEASIBLE:
        if plan.objective_breakdown is not None:
            raise PlanValidationError("infeasible plan must not contain objective breakdown")
        return

    validate_plan(problem, plan)
    if plan.objective_breakdown is None:
        raise PlanValidationError("multi-objective feasible plan lacks objective breakdown")

    expected = objective_breakdown_for_plan(problem, policy, plan)
    if plan.objective_breakdown != expected:
        raise PlanValidationError("objective breakdown does not match plan and policy")

    baseline = build_plan(problem)
    if baseline.status is not PlanStatus.FEASIBLE:
        raise PlanValidationError("multi-objective plan is feasible while baseline is infeasible")
    if plan.minimum_required_cost != baseline.total_cost:
        raise PlanValidationError(
            "multi-objective minimum_required_cost does not match cost-only baseline"
        )
