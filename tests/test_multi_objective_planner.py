from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import localcontext

import pytest

from household_supply import (
    Demand,
    InventorySnapshot,
    Item,
    MarketSnapshot,
    Money,
    MultiObjectivePolicy,
    ObjectiveBreakdown,
    Offer,
    PlanningPolicy,
    PlanningProblem,
    Quantity,
    SKU,
    SurplusPenaltyRate,
    build_multi_objective_plan,
    build_plan,
    validate_multi_objective_plan,
)
from household_supply.planning import PlanValidationError


NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


def make_offer(
    offer_id: str,
    item: Item,
    seller: str,
    package: str,
    unit: str,
    price: str,
) -> Offer:
    sku = SKU(
        f"sku-{offer_id}",
        item,
        offer_id,
        Quantity(package, unit),
    )
    return Offer(
        offer_id,
        sku,
        seller,
        Money(price, "KGS"),
        NOW,
        "fixture",
    )


def problem_for(
    demands: tuple[Demand, ...], offers: tuple[Offer, ...], budget: str = "3000"
) -> PlanningProblem:
    return PlanningProblem(
        demands=demands,
        inventory=InventorySnapshot(),
        market=MarketSnapshot(NOW, offers),
        policy=PlanningPolicy(Money(budget, "KGS")),
    )


def purchase_ids(plan) -> tuple[str, ...]:
    return tuple(sorted(purchase.offer.id for purchase in plan.purchases))


def test_zero_objective_policy_matches_m1_baseline() -> None:
    rice = Item("rice", "Rice")
    milk = Item("milk", "Milk")
    problem = problem_for(
        (
            Demand(rice, Quantity("700", "g")),
            Demand(milk, Quantity("900", "ml")),
        ),
        (
            make_offer("rice-a", rice, "store-a", "500", "g", "70"),
            make_offer("rice-b", rice, "store-b", "800", "g", "100"),
            make_offer("milk-a", milk, "store-a", "1000", "ml", "95"),
            make_offer("milk-b", milk, "store-b", "930", "ml", "82"),
        ),
    )

    baseline = build_plan(problem)
    optimized = build_multi_objective_plan(problem, MultiObjectivePolicy.zero("KGS"))

    assert optimized.purchases == baseline.purchases
    assert optimized.total_cost == baseline.total_cost
    assert optimized.projected_leftovers == baseline.projected_leftovers
    assert optimized.minimum_required_cost == baseline.total_cost
    assert optimized.objective_breakdown is not None
    assert optimized.objective_breakdown.total_score == baseline.total_cost


def test_additional_store_penalty_can_prefer_one_store() -> None:
    rice = Item("rice", "Rice")
    milk = Item("milk", "Milk")
    problem = problem_for(
        (Demand(rice, Quantity(1, "kg")), Demand(milk, Quantity(1, "l"))),
        (
            make_offer("rice-a", rice, "store-a", "1", "kg", "100"),
            make_offer("milk-a", milk, "store-a", "1", "l", "100"),
            make_offer("rice-b", rice, "store-b", "1", "kg", "80"),
            make_offer("milk-c", milk, "store-c", "1", "l", "80"),
        ),
    )

    baseline = build_plan(problem)
    optimized = build_multi_objective_plan(
        problem,
        MultiObjectivePolicy(additional_store_penalty=Money(100, "KGS")),
    )

    assert baseline.total_cost == Money(160, "KGS")
    assert {purchase.offer.seller_id for purchase in baseline.purchases} == {
        "store-b",
        "store-c",
    }
    assert optimized.total_cost == Money(200, "KGS")
    assert {purchase.offer.seller_id for purchase in optimized.purchases} == {"store-a"}
    assert optimized.minimum_required_cost == Money(160, "KGS")
    assert optimized.objective_breakdown == ObjectiveBreakdown(
        purchase_cost=Money(200, "KGS"),
        surplus_penalty=Money(0, "KGS"),
        additional_store_penalty=Money(0, "KGS"),
        total_score=Money(200, "KGS"),
        selected_sellers=("store-a",),
        additional_store_count=0,
    )
    assert any("spends 40 KGS more" in line for line in optimized.explanation)


def test_surplus_penalty_can_prefer_more_expensive_exact_package() -> None:
    rice = Item("rice", "Rice")
    problem = problem_for(
        (Demand(rice, Quantity("600", "g")),),
        (
            make_offer("large", rice, "store-a", "1000", "g", "100"),
            make_offer("exact", rice, "store-b", "600", "g", "110"),
        ),
    )
    policy = MultiObjectivePolicy(
        additional_store_penalty=Money(0, "KGS"),
        surplus_penalties=(
            SurplusPenaltyRate("rice", Money("0.05", "KGS")),
        ),
    )

    baseline = build_plan(problem)
    optimized = build_multi_objective_plan(problem, policy)

    assert purchase_ids(baseline) == ("large",)
    assert baseline.total_cost == Money(100, "KGS")
    assert purchase_ids(optimized) == ("exact",)
    assert optimized.total_cost == Money(110, "KGS")
    assert optimized.objective_breakdown is not None
    assert optimized.objective_breakdown.surplus_penalty == Money(0, "KGS")
    assert optimized.objective_breakdown.total_score == Money(110, "KGS")


def test_soft_objective_score_does_not_consume_hard_budget() -> None:
    rice = Item("rice", "Rice")
    milk = Item("milk", "Milk")
    problem = problem_for(
        (Demand(rice, Quantity(1, "kg")), Demand(milk, Quantity(1, "l"))),
        (
            make_offer("rice-b", rice, "store-b", "1", "kg", "80"),
            make_offer("milk-c", milk, "store-c", "1", "l", "80"),
        ),
        budget="160",
    )
    policy = MultiObjectivePolicy(additional_store_penalty=Money(500, "KGS"))

    plan = build_multi_objective_plan(problem, policy)

    assert plan.total_cost == Money(160, "KGS")
    assert plan.budget_remaining == Money(0, "KGS")
    assert plan.objective_breakdown is not None
    assert plan.objective_breakdown.total_score == Money(660, "KGS")


def test_budget_can_rule_out_soft_objective_preferred_plan() -> None:
    rice = Item("rice", "Rice")
    problem = problem_for(
        (Demand(rice, Quantity("600", "g")),),
        (
            make_offer("large", rice, "store-a", "1000", "g", "100"),
            make_offer("exact", rice, "store-b", "600", "g", "110"),
        ),
        budget="105",
    )
    policy = MultiObjectivePolicy(
        additional_store_penalty=Money(0, "KGS"),
        surplus_penalties=(SurplusPenaltyRate("rice", Money("1", "KGS")),),
    )

    plan = build_multi_objective_plan(problem, policy)

    assert purchase_ids(plan) == ("large",)
    assert plan.total_cost == Money(100, "KGS")
    assert plan.objective_breakdown is not None
    assert plan.objective_breakdown.surplus_penalty == Money(400, "KGS")


def test_same_seller_across_items_does_not_add_store_penalty() -> None:
    rice = Item("rice", "Rice")
    milk = Item("milk", "Milk")
    problem = problem_for(
        (Demand(rice, Quantity(1, "kg")), Demand(milk, Quantity(1, "l"))),
        (
            make_offer("rice", rice, "store-a", "1", "kg", "100"),
            make_offer("milk", milk, "store-a", "1", "l", "100"),
        ),
    )
    policy = MultiObjectivePolicy(additional_store_penalty=Money(999, "KGS"))

    plan = build_multi_objective_plan(problem, policy)

    assert plan.objective_breakdown is not None
    assert plan.objective_breakdown.selected_sellers == ("store-a",)
    assert plan.objective_breakdown.additional_store_count == 0
    assert plan.objective_breakdown.additional_store_penalty == Money(0, "KGS")


def test_inventory_only_plan_has_no_store_penalty() -> None:
    rice = Item("rice", "Rice")
    from household_supply import InventoryLot

    problem = PlanningProblem(
        demands=(Demand(rice, Quantity(500, "g")),),
        inventory=InventorySnapshot((InventoryLot("rice-home", rice, Quantity(1, "kg")),)),
        market=MarketSnapshot(NOW, ()),
        policy=PlanningPolicy(Money(100, "KGS")),
    )

    plan = build_multi_objective_plan(
        problem, MultiObjectivePolicy(additional_store_penalty=Money(500, "KGS"))
    )

    assert plan.purchases == ()
    assert plan.total_cost == Money(0, "KGS")
    assert plan.objective_breakdown is not None
    assert plan.objective_breakdown.selected_sellers == ()
    assert plan.objective_breakdown.additional_store_penalty == Money(0, "KGS")


def test_multi_objective_policy_rejects_duplicate_surplus_rates() -> None:
    with pytest.raises(ValueError, match="duplicate surplus"):
        MultiObjectivePolicy(
            additional_store_penalty=Money(0, "KGS"),
            surplus_penalties=(
                SurplusPenaltyRate("rice", Money("0.1", "KGS")),
                SurplusPenaltyRate("rice", Money("0.2", "KGS")),
            ),
        )


def test_multi_objective_policy_freezes_mutable_penalty_input() -> None:
    penalties = [SurplusPenaltyRate("rice", Money("0.1", "KGS"))]
    policy = MultiObjectivePolicy(
        additional_store_penalty=Money(0, "KGS"),
        surplus_penalties=penalties,
    )

    penalties.append(SurplusPenaltyRate("milk", Money("0.2", "KGS")))

    assert policy.surplus_penalties == (
        SurplusPenaltyRate("rice", Money("0.1", "KGS")),
    )


def test_mutating_original_penalty_list_cannot_change_existing_policy_plan() -> None:
    rice = Item("rice", "Rice")
    problem = problem_for(
        (Demand(rice, Quantity("600", "g")),),
        (
            make_offer("large", rice, "store-a", "1000", "g", "100"),
            make_offer("exact", rice, "store-b", "600", "g", "110"),
        ),
    )
    penalties = []
    policy = MultiObjectivePolicy(
        additional_store_penalty=Money(0, "KGS"),
        surplus_penalties=penalties,
    )

    before = build_multi_objective_plan(problem, policy)
    penalties.append(SurplusPenaltyRate("rice", Money("1", "KGS")))
    after = build_multi_objective_plan(problem, policy)

    assert purchase_ids(before) == ("large",)
    assert after == before


def test_surplus_penalty_item_id_is_canonicalized_before_duplicate_check() -> None:
    with pytest.raises(ValueError, match="duplicate surplus"):
        MultiObjectivePolicy(
            additional_store_penalty=Money(0, "KGS"),
            surplus_penalties=(
                SurplusPenaltyRate(" rice ", Money("0.1", "KGS")),
                SurplusPenaltyRate("rice", Money("0.2", "KGS")),
            ),
        )


def test_planner_rejects_objective_currency_different_from_budget() -> None:
    rice = Item("rice", "Rice")
    problem = problem_for(
        (Demand(rice, Quantity(1, "kg")),),
        (make_offer("rice", rice, "store-a", "1", "kg", "100"),),
    )
    policy = MultiObjectivePolicy(additional_store_penalty=Money(1, "USD"))

    with pytest.raises(ValueError, match="currency"):
        build_multi_objective_plan(problem, policy)


def test_multi_objective_validator_rejects_falsified_breakdown() -> None:
    rice = Item("rice", "Rice")
    problem = problem_for(
        (Demand(rice, Quantity(1, "kg")),),
        (make_offer("rice", rice, "store-a", "1", "kg", "100"),),
    )
    policy = MultiObjectivePolicy(additional_store_penalty=Money(50, "KGS"))
    plan = build_multi_objective_plan(problem, policy)
    assert plan.objective_breakdown is not None

    falsified = replace(
        plan,
        objective_breakdown=ObjectiveBreakdown(
            purchase_cost=Money(100, "KGS"),
            surplus_penalty=Money(1, "KGS"),
            additional_store_penalty=Money(0, "KGS"),
            total_score=Money(101, "KGS"),
            selected_sellers=("store-a",),
            additional_store_count=0,
        ),
    )

    with pytest.raises(PlanValidationError, match="objective breakdown"):
        validate_multi_objective_plan(problem, policy, falsified)


def test_multi_objective_planner_is_independent_of_market_offer_order() -> None:
    rice = Item("rice", "Rice")
    milk = Item("milk", "Milk")
    offers = (
        make_offer("rice-a", rice, "store-a", "1", "kg", "100"),
        make_offer("rice-b", rice, "store-b", "1", "kg", "80"),
        make_offer("milk-a", milk, "store-a", "1", "l", "100"),
        make_offer("milk-c", milk, "store-c", "1", "l", "80"),
    )
    demands = (Demand(rice, Quantity(1, "kg")), Demand(milk, Quantity(1, "l")))
    policy = MultiObjectivePolicy(additional_store_penalty=Money(100, "KGS"))

    forward = build_multi_objective_plan(problem_for(demands, offers), policy)
    reversed_plan = build_multi_objective_plan(
        problem_for(tuple(reversed(demands)), tuple(reversed(offers))), policy
    )

    assert purchase_ids(forward) == purchase_ids(reversed_plan)
    assert forward.total_cost == reversed_plan.total_cost
    assert forward.objective_breakdown == reversed_plan.objective_breakdown


def test_multi_objective_planner_is_independent_of_ambient_decimal_context() -> None:
    rice = Item("rice", "Rice")
    problem = problem_for(
        (Demand(rice, Quantity("0.333333333333", "kg")),),
        (
            make_offer("large", rice, "store-a", "500", "g", "100.123456789"),
            make_offer("small", rice, "store-b", "334", "g", "105.123456789"),
        ),
    )
    policy = MultiObjectivePolicy(
        additional_store_penalty=Money("7.123456789", "KGS"),
        surplus_penalties=(
            SurplusPenaltyRate("rice", Money("0.123456789", "KGS")),
        ),
    )

    plans = []
    for precision in (6, 12, 28, 50):
        with localcontext() as context:
            context.prec = precision
            plans.append(build_multi_objective_plan(problem, policy))

    assert plans == [plans[0]] * 4


def test_zero_policy_preserves_m1_tie_breaking_exactly() -> None:
    first = Item("first", "First")
    second = Item("second", "Second")
    problem = problem_for(
        (Demand(first, Quantity(800, "g")), Demand(second, Quantity(200, "g"))),
        (
            make_offer("first-500", first, "store-1", "500", "g", "65"),
            make_offer("first-800", first, "store-2", "800", "g", "126"),
            make_offer("second-100", second, "store-2", "100", "g", "38"),
            make_offer("second-700", second, "store-2", "700", "g", "76"),
        ),
    )

    baseline = build_plan(problem)
    optimized = build_multi_objective_plan(problem, MultiObjectivePolicy.zero("KGS"))

    assert [(p.offer.id, p.packs) for p in optimized.purchases] == [
        (p.offer.id, p.packs) for p in baseline.purchases
    ]
    assert optimized.projected_leftovers == baseline.projected_leftovers


def test_multi_objective_preserves_m1_infeasible_result() -> None:
    rice = Item("rice", "Rice")
    problem = problem_for(
        (Demand(rice, Quantity(1, "kg")),),
        (make_offer("rice", rice, "store-a", "1", "kg", "200"),),
        budget="100",
    )

    plan = build_multi_objective_plan(
        problem, MultiObjectivePolicy(additional_store_penalty=Money(50, "KGS"))
    )

    assert plan.status.value == "infeasible"
    assert plan.objective_breakdown is None
    assert plan.minimum_required_cost == Money(200, "KGS")


def test_multi_objective_validator_rejects_wrong_minimum_cost_floor() -> None:
    rice = Item("rice", "Rice")
    problem = problem_for(
        (Demand(rice, Quantity(1, "kg")),),
        (make_offer("rice", rice, "store-a", "1", "kg", "100"),),
    )
    policy = MultiObjectivePolicy(additional_store_penalty=Money(0, "KGS"))
    plan = build_multi_objective_plan(problem, policy)
    falsified = replace(plan, minimum_required_cost=Money(999, "KGS"))

    with pytest.raises(PlanValidationError, match="minimum_required_cost"):
        validate_multi_objective_plan(problem, policy, falsified)


def test_surplus_penalty_is_based_on_net_requirement_after_inventory() -> None:
    from household_supply import InventoryLot

    rice = Item("rice", "Rice")
    offers = (
        make_offer("net-exact", rice, "store-a", "600", "g", "100"),
        make_offer("large", rice, "store-b", "1000", "g", "90"),
    )
    problem = PlanningProblem(
        demands=(Demand(rice, Quantity(1, "kg")),),
        inventory=InventorySnapshot(
            (InventoryLot("home-rice", rice, Quantity(400, "g")),)
        ),
        market=MarketSnapshot(NOW, offers),
        policy=PlanningPolicy(Money(1000, "KGS")),
    )
    policy = MultiObjectivePolicy(
        additional_store_penalty=Money(0, "KGS"),
        surplus_penalties=(SurplusPenaltyRate("rice", Money("0.1", "KGS")),),
    )

    baseline = build_plan(problem)
    optimized = build_multi_objective_plan(problem, policy)

    assert purchase_ids(baseline) == ("large",)
    assert purchase_ids(optimized) == ("net-exact",)
    assert optimized.objective_breakdown is not None
    assert optimized.objective_breakdown.surplus_penalty == Money(0, "KGS")
