from __future__ import annotations

from decimal import Decimal

from household_supply.domain import (
    Money,
    MultiObjectivePolicy,
    ObjectiveBreakdown,
    PlanningProblem,
    ProcurementPlan,
)
from household_supply.domain._decimal import (
    add_decimals_exact,
    multiply_decimal_by_int_exact,
    multiply_decimals_exact,
    subtract_decimals_exact,
)

from .compile import CompiledRequirement, compile_requirements


def validate_objective_policy_currency(
    problem: PlanningProblem, policy: MultiObjectivePolicy
) -> None:
    currency = problem.policy.budget.currency
    if policy.additional_store_penalty.currency != currency:
        raise ValueError("objective policy currency must match planning budget currency")
    for penalty in policy.surplus_penalties:
        if penalty.cost_per_base_unit.currency != currency:
            raise ValueError("surplus penalty currency must match planning budget currency")


def surplus_penalty_amount(
    policy: MultiObjectivePolicy,
    requirement: CompiledRequirement,
    purchased_base_amount: Decimal,
) -> Money:
    surplus = max(
        Decimal("0"),
        subtract_decimals_exact(
            purchased_base_amount, requirement.net_required.base_amount
        ),
    )
    rate = policy.surplus_rate_for(requirement.item_id)
    return Money(
        multiply_decimals_exact(rate.amount, surplus),
        rate.currency,
    )


def additional_store_penalty(
    policy: MultiObjectivePolicy, selected_sellers: frozenset[str]
) -> Money:
    additional = max(len(selected_sellers) - 1, 0)
    return Money(
        multiply_decimal_by_int_exact(policy.additional_store_penalty.amount, additional),
        policy.additional_store_penalty.currency,
    )


def objective_breakdown_for_plan(
    problem: PlanningProblem,
    policy: MultiObjectivePolicy,
    plan: ProcurementPlan,
) -> ObjectiveBreakdown:
    requirements = {entry.item_id: entry for entry in compile_requirements(problem)}
    purchased_by_item: dict[str, Decimal] = {}
    selected_sellers: set[str] = set()

    for purchase in plan.purchases:
        item_id = purchase.offer.sku.item.id
        purchased_by_item[item_id] = add_decimals_exact(
            purchased_by_item.get(item_id, Decimal("0")),
            purchase.acquired_quantity.as_base().base_amount,
        )
        selected_sellers.add(purchase.offer.seller_id)

    surplus_penalty = Money.zero(problem.policy.budget.currency)
    for item_id, requirement in requirements.items():
        surplus_penalty = surplus_penalty + surplus_penalty_amount(
            policy,
            requirement,
            purchased_by_item.get(item_id, Decimal("0")),
        )

    sellers = tuple(sorted(selected_sellers))
    store_penalty = additional_store_penalty(policy, frozenset(selected_sellers))
    return ObjectiveBreakdown(
        purchase_cost=plan.total_cost,
        surplus_penalty=surplus_penalty,
        additional_store_penalty=store_penalty,
        total_score=plan.total_cost + surplus_penalty + store_penalty,
        selected_sellers=sellers,
        additional_store_count=max(len(sellers) - 1, 0),
    )
