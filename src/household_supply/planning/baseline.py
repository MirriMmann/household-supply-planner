from __future__ import annotations

from household_supply.domain import Money, PlanStatus, PlanningProblem, ProcurementPlan

from .assemble import assemble_feasible_plan
from .candidates import baseline_candidate_key, enumerate_item_candidates
from .compile import compile_requirements


def build_plan(problem: PlanningProblem) -> ProcurementPlan:
    currency = problem.policy.budget.currency
    requirements = compile_requirements(problem)
    selected = {}
    unavailable: list[str] = []

    for requirement in requirements:
        candidates = enumerate_item_candidates(problem, requirement)
        if not candidates:
            unavailable.append(requirement.item_id)
            continue
        selected[requirement.item_id] = min(candidates, key=baseline_candidate_key)

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
    for candidate in selected.values():
        minimum_required_cost = minimum_required_cost + candidate.purchase_cost

    if minimum_required_cost.amount > problem.policy.budget.amount:
        shortage = minimum_required_cost - problem.policy.budget
        return ProcurementPlan(
            status=PlanStatus.INFEASIBLE,
            purchases=(),
            requirement_coverage=(),
            projected_leftovers=(),
            total_cost=Money.zero(currency),
            budget_remaining=problem.policy.budget,
            minimum_required_cost=minimum_required_cost,
            infeasibility_reasons=(
                f"minimum required purchase cost exceeds budget by "
                f"{shortage.amount} {currency}",
            ),
            explanation=(
                f"minimum package-aware cost is {minimum_required_cost.amount} {currency}, "
                f"budget is {problem.policy.budget.amount} {currency}",
            ),
        )

    plan = assemble_feasible_plan(
        problem,
        requirements,
        selected,
        minimum_required_cost=minimum_required_cost,
    )

    from .validate import validate_plan

    validate_plan(problem, plan)
    return plan
