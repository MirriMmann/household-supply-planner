from __future__ import annotations

from decimal import Decimal

from household_supply.domain import PlanStatus, PlanningProblem, ProcurementPlan


class PlanValidationError(ValueError):
    """Raised when a feasible procurement plan violates M1 invariants."""


def validate_plan(problem: PlanningProblem, plan: ProcurementPlan) -> None:
    if plan.status is not PlanStatus.FEASIBLE:
        return

    budget = problem.policy.budget
    if plan.total_cost.currency != budget.currency:
        raise PlanValidationError("plan total currency differs from budget currency")
    if plan.total_cost.amount > budget.amount:
        raise PlanValidationError("feasible plan exceeds hard budget")

    expected_cost = Decimal("0")
    purchased_by_item: dict[str, Decimal] = {}
    for purchase in plan.purchases:
        if purchase.packs <= 0:
            raise PlanValidationError("purchase contains non-positive package count")
        if not purchase.offer.available:
            raise PlanValidationError("purchase uses unavailable offer")
        if purchase.offer.price.currency != budget.currency:
            raise PlanValidationError("purchase currency differs from budget currency")
        expected_cost += purchase.offer.price.amount * purchase.packs
        expected_quantity = (
            purchase.offer.sku.package_quantity.as_base().base_amount * purchase.packs
        )
        if purchase.acquired_quantity.as_base().base_amount != expected_quantity:
            raise PlanValidationError("purchase quantity does not match package count")
        item_id = purchase.offer.sku.item.id
        purchased_by_item[item_id] = (
            purchased_by_item.get(item_id, Decimal("0")) + expected_quantity
        )

    if expected_cost != plan.total_cost.amount:
        raise PlanValidationError("plan total cost does not match purchases")

    coverage_by_item = {entry.item_id: entry for entry in plan.requirement_coverage}
    for item_id, coverage in coverage_by_item.items():
        if coverage.covered.base_amount < coverage.required.base_amount:
            raise PlanValidationError(f"requirement is under-covered: {item_id}")
        if coverage.purchased.base_amount != purchased_by_item.get(item_id, Decimal("0")):
            raise PlanValidationError(f"coverage purchase mismatch: {item_id}")
