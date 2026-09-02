from __future__ import annotations

from dataclasses import dataclass
from itertools import product

from household_supply.domain import (
    Money,
    MultiObjectivePolicy,
    ObjectiveBreakdown,
    PlanStatus,
    PlanningProblem,
    ProcurementPlan,
)

from .assemble import assemble_feasible_plan
from .baseline import build_plan
from .candidates import (
    ItemCandidate,
    baseline_candidate_key,
    enumerate_item_candidates,
)
from .compile import CompiledRequirement, compile_requirements
from .objective import (
    additional_store_penalty,
    objective_breakdown_for_plan,
    surplus_penalty_amount,
    validate_objective_policy_currency,
)


_MAX_GLOBAL_COMBINATIONS = 300_000


@dataclass(frozen=True, slots=True)
class _ScoredCandidate:
    candidate: ItemCandidate
    surplus_penalty: Money
    local_score: Money


def _surplus_penalty(
    policy: MultiObjectivePolicy,
    requirement: CompiledRequirement,
    candidate: ItemCandidate,
) -> Money:
    return surplus_penalty_amount(
        policy, requirement, candidate.purchased.base_amount
    )


def _score_candidates(
    problem: PlanningProblem,
    policy: MultiObjectivePolicy,
    requirement: CompiledRequirement,
) -> tuple[_ScoredCandidate, ...]:
    candidates = enumerate_item_candidates(problem, requirement)
    scored = []
    for candidate in candidates:
        penalty = _surplus_penalty(policy, requirement, candidate)
        scored.append(
            _ScoredCandidate(
                candidate=candidate,
                surplus_penalty=penalty,
                local_score=candidate.purchase_cost + penalty,
            )
        )
    return _pareto_prune_same_sellers(tuple(scored))


def _pareto_prune_same_sellers(
    candidates: tuple[_ScoredCandidate, ...],
) -> tuple[_ScoredCandidate, ...]:
    """Keep budget-relevant local Pareto candidates for each exact seller set.

    Within one seller set, only purchase cost and local objective score can
    affect a later global choice. Sorting by cost lets us retain the descending
    score frontier in O(n log n) instead of pairwise O(n²) comparison.
    """

    grouped: dict[frozenset[str], list[_ScoredCandidate]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate.candidate.sellers, []).append(candidate)

    retained: list[_ScoredCandidate] = []
    for seller_set in sorted(grouped, key=lambda sellers: tuple(sorted(sellers))):
        ordered = sorted(
            grouped[seller_set],
            key=lambda scored: (
                scored.candidate.purchase_cost.amount,
                scored.local_score.amount,
                baseline_candidate_key(scored.candidate),
            ),
        )
        best_score = None
        for candidate in ordered:
            score = candidate.local_score.amount
            if best_score is not None and best_score <= score:
                continue
            retained.append(candidate)
            best_score = score

    retained.sort(
        key=lambda scored: (
            tuple(sorted(scored.candidate.sellers)),
            scored.local_score.amount,
            scored.candidate.purchase_cost.amount,
            scored.surplus_penalty.amount,
            scored.candidate.surplus.base_amount,
            scored.candidate.total_packs,
            scored.candidate.count_signature,
        )
    )
    return tuple(retained)

def _evaluate_selected(
    policy: MultiObjectivePolicy,
    selected: tuple[_ScoredCandidate, ...],
) -> tuple[Money, Money, Money, frozenset[str], int]:
    currency = policy.additional_store_penalty.currency
    purchase_cost = Money.zero(currency)
    surplus_penalty = Money.zero(currency)
    sellers: set[str] = set()
    total_packs = 0

    for scored in selected:
        purchase_cost = purchase_cost + scored.candidate.purchase_cost
        surplus_penalty = surplus_penalty + scored.surplus_penalty
        sellers.update(scored.candidate.sellers)
        total_packs += scored.candidate.total_packs

    seller_set = frozenset(sellers)
    visit_penalty = additional_store_penalty(policy, seller_set)
    total_score = purchase_cost + surplus_penalty + visit_penalty
    return purchase_cost, surplus_penalty, total_score, seller_set, total_packs


def build_multi_objective_plan(
    problem: PlanningProblem,
    policy: MultiObjectivePolicy,
) -> ProcurementPlan:
    validate_objective_policy_currency(problem, policy)

    baseline = build_plan(problem)
    if baseline.status is not PlanStatus.FEASIBLE:
        return baseline

    requirements = compile_requirements(problem)
    candidates_by_item: list[tuple[_ScoredCandidate, ...]] = []
    global_combinations = 1

    for requirement in requirements:
        candidates = _score_candidates(problem, policy, requirement)
        if not candidates:
            raise RuntimeError(
                f"M3 candidate generation lost feasible item: {requirement.item_id}"
            )
        candidates_by_item.append(candidates)
        global_combinations *= len(candidates)

    if global_combinations > _MAX_GLOBAL_COMBINATIONS:
        raise RuntimeError(
            f"multi-objective search space is too large: "
            f"{global_combinations} combinations"
        )

    best_key = None
    best_selected = None
    best_breakdown = None

    for selected in product(*candidates_by_item):
        purchase_cost, surplus_penalty, total_score, sellers, total_packs = (
            _evaluate_selected(policy, selected)
        )
        if purchase_cost.amount > problem.policy.budget.amount:
            continue
        store_penalty = additional_store_penalty(policy, sellers)
        baseline_tie = tuple(
            baseline_candidate_key(scored.candidate) for scored in selected
        )
        key = (
            total_score.amount,
            purchase_cost.amount,
            surplus_penalty.amount,
            store_penalty.amount,
            baseline_tie,
            total_packs,
            tuple(sorted(sellers)),
        )
        if best_key is None or key < best_key:
            best_key = key
            best_selected = selected
            seller_tuple = tuple(sorted(sellers))
            best_breakdown = ObjectiveBreakdown(
                purchase_cost=purchase_cost,
                surplus_penalty=surplus_penalty,
                additional_store_penalty=store_penalty,
                total_score=total_score,
                selected_sellers=seller_tuple,
                additional_store_count=max(len(seller_tuple) - 1, 0),
            )

    if best_selected is None or best_breakdown is None:
        raise RuntimeError("baseline is feasible but M3 found no budget-feasible combination")

    selected_by_item = {
        requirement.item_id: scored.candidate
        for requirement, scored in zip(requirements, best_selected, strict=True)
    }

    baseline_breakdown = objective_breakdown_for_plan(problem, policy, baseline)
    extra_spend = best_breakdown.purchase_cost - baseline.total_cost
    explanation = [
        f"multi-objective score: {best_breakdown.total_score.amount} "
        f"{best_breakdown.total_score.currency}",
        f"purchase cost: {best_breakdown.purchase_cost.amount} "
        f"{best_breakdown.purchase_cost.currency}",
        f"surplus penalty: {best_breakdown.surplus_penalty.amount} "
        f"{best_breakdown.surplus_penalty.currency}",
        f"additional-store penalty: {best_breakdown.additional_store_penalty.amount} "
        f"{best_breakdown.additional_store_penalty.currency}",
        "selected sellers: "
        + (", ".join(best_breakdown.selected_sellers) or "none"),
    ]
    if best_breakdown.total_score.amount < baseline_breakdown.total_score.amount:
        explanation.append(
            f"selected plan improves configured objective versus cost-only baseline: "
            f"{baseline_breakdown.total_score.amount} -> "
            f"{best_breakdown.total_score.amount} {best_breakdown.total_score.currency}"
        )
    if extra_spend.amount > 0:
        explanation.append(
            f"selected plan spends {extra_spend.amount} {extra_spend.currency} more than "
            "the minimum purchase-cost baseline to reduce configured soft penalties"
        )

    plan = assemble_feasible_plan(
        problem,
        requirements,
        selected_by_item,
        minimum_required_cost=baseline.total_cost,
        objective_breakdown=best_breakdown,
        explanation_prefix=tuple(explanation),
    )

    from .validate import validate_multi_objective_plan, validate_plan

    validate_plan(problem, plan)
    validate_multi_objective_plan(problem, policy, plan)
    return plan
