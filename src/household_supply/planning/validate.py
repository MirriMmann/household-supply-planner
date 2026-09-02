from __future__ import annotations

from decimal import Decimal

from household_supply.domain import PlanStatus, PlanningProblem, ProcurementPlan, Quantity

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
        expected_cost += expected_purchase_cost.amount

        expected_quantity = (
            purchase.offer.sku.package_quantity.as_base().base_amount * purchase.packs
        )
        acquired = purchase.acquired_quantity.as_base()
        requirement = requirements_by_item[item_id]
        if not acquired.compatible_with(requirement.required):
            raise PlanValidationError(f"purchase quantity has incompatible unit: {item_id}")
        if acquired.base_amount != expected_quantity:
            raise PlanValidationError("purchase quantity does not match package count")
        purchased_by_item[item_id] = (
            purchased_by_item.get(item_id, Decimal("0")) + expected_quantity
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

        supplied_amount = requirement.inventory_used.base_amount + purchased_amount
        expected_covered = Quantity(
            min(supplied_amount, requirement.required.base_amount),
            requirement.required.base_unit,
        )
        if coverage.covered != expected_covered:
            raise PlanValidationError(f"coverage covered quantity mismatch: {item_id}")
        if coverage.covered.base_amount < requirement.required.base_amount:
            raise PlanValidationError(f"requirement is under-covered: {item_id}")

        expected_leftover_amount = (
            requirement.inventory_available.base_amount
            - requirement.inventory_used.base_amount
            + max(Decimal("0"), supplied_amount - requirement.required.base_amount)
        )
        expected_leftover = Quantity(
            expected_leftover_amount, requirement.required.base_unit
        )
        if leftovers_by_item[item_id].quantity != expected_leftover:
            raise PlanValidationError(f"projected leftover mismatch: {item_id}")
