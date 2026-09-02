from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from household_supply.domain import (
    Demand,
    InventoryLot,
    InventorySnapshot,
    Item,
    MarketSnapshot,
    Money,
    Offer,
    PlanStatus,
    PlanningPolicy,
    PlanningProblem,
    Quantity,
    SKU,
)
from household_supply.planning import build_plan


NOW = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


def item(item_id: str) -> Item:
    return Item(item_id, item_id.replace("_", " "))


def sku(sku_id: str, product: Item, amount: str | int, unit: str) -> SKU:
    return SKU(sku_id, product, sku_id, Quantity(amount, unit))


def offer(
    offer_id: str,
    product_sku: SKU,
    price: str | int,
    *,
    seller: str = "store-a",
    currency: str = "KGS",
    available: bool = True,
) -> Offer:
    return Offer(
        id=offer_id,
        sku=product_sku,
        seller_id=seller,
        price=Money(price, currency),
        observed_at=NOW,
        source="fixture",
        available=available,
    )


def problem(
    *,
    demands: tuple[Demand, ...],
    offers: tuple[Offer, ...] = (),
    inventory: tuple[InventoryLot, ...] = (),
    budget: str | int = "3000",
) -> PlanningProblem:
    return PlanningProblem(
        demands=demands,
        inventory=InventorySnapshot(inventory),
        market=MarketSnapshot(NOW, offers),
        policy=PlanningPolicy(Money(budget, "KGS")),
    )


def coverage(plan, item_id: str):
    return next(entry for entry in plan.requirement_coverage if entry.item_id == item_id)


def leftover(plan, item_id: str):
    return next(entry for entry in plan.projected_leftovers if entry.item_id == item_id)


def test_existing_inventory_fully_covers_demand() -> None:
    rice = item("rice")
    plan = build_plan(
        problem(
            demands=(Demand(rice, Quantity("900", "g")),),
            inventory=(InventoryLot("rice-home", rice, Quantity("1", "kg")),),
        )
    )

    assert plan.status is PlanStatus.FEASIBLE
    assert plan.purchases == ()
    assert plan.total_cost.amount == Decimal("0")
    assert coverage(plan, "rice").inventory_used.amount == Decimal("900")
    assert leftover(plan, "rice").quantity.amount == Decimal("100")


def test_inventory_partially_reduces_purchase_requirement() -> None:
    rice = item("rice")
    rice_800 = sku("rice-800", rice, 800, "g")
    plan = build_plan(
        problem(
            demands=(Demand(rice, Quantity("900", "g")),),
            inventory=(InventoryLot("rice-home", rice, Quantity("350", "g")),),
            offers=(offer("rice-a", rice_800, 120),),
        )
    )

    assert plan.status is PlanStatus.FEASIBLE
    assert len(plan.purchases) == 1
    assert plan.purchases[0].packs == 1
    assert coverage(plan, "rice").inventory_used.amount == Decimal("350")
    assert coverage(plan, "rice").purchased.amount == Decimal("800")
    assert leftover(plan, "rice").quantity.amount == Decimal("250")


def test_purchase_rounds_up_to_whole_packages() -> None:
    milk = item("milk")
    milk_930 = sku("milk-930", milk, 930, "ml")
    plan = build_plan(
        problem(
            demands=(Demand(milk, Quantity("1500", "ml")),),
            offers=(offer("milk-a", milk_930, 82),),
        )
    )

    assert plan.purchases[0].packs == 2
    assert plan.purchases[0].acquired_quantity.amount == Decimal("1860")
    assert leftover(plan, "milk").quantity.amount == Decimal("360")


def test_multiple_packages_of_same_sku_are_supported() -> None:
    chicken = item("chicken")
    chicken_500 = sku("chicken-500", chicken, 500, "g")
    plan = build_plan(
        problem(
            demands=(Demand(chicken, Quantity("1.2", "kg")),),
            offers=(offer("chicken-a", chicken_500, 180),),
        )
    )

    assert plan.purchases[0].packs == 3
    assert plan.total_cost.amount == Decimal("540")


def test_cheapest_unit_price_need_not_be_cheapest_required_purchase() -> None:
    rice = item("rice")
    large = sku("large", rice, 1000, "g")
    small = sku("small", rice, 300, "g")

    plan = build_plan(
        problem(
            demands=(Demand(rice, Quantity("500", "g")),),
            offers=(
                offer("large-offer", large, 100),  # 0.10 KGS/g
                offer("small-offer", small, 40),   # 0.133 KGS/g, but 2 packs cost 80
            ),
        )
    )

    assert [(p.offer.id, p.packs) for p in plan.purchases] == [("small-offer", 2)]
    assert plan.total_cost.amount == Decimal("80")


def test_unavailable_offer_is_never_selected() -> None:
    rice = item("rice")
    rice_1kg = sku("rice-1kg", rice, 1, "kg")
    plan = build_plan(
        problem(
            demands=(Demand(rice, Quantity("1", "kg")),),
            offers=(
                offer("sold-out", rice_1kg, 1, available=False),
                offer("available", rice_1kg, 120),
            ),
        )
    )

    assert [purchase.offer.id for purchase in plan.purchases] == ["available"]


def test_foreign_currency_offer_is_not_silently_mixed() -> None:
    rice = item("rice")
    rice_1kg = sku("rice-1kg", rice, 1, "kg")
    plan = build_plan(
        problem(
            demands=(Demand(rice, Quantity("1", "kg")),),
            offers=(
                offer("usd", rice_1kg, 1, currency="USD"),
                offer("kgs", rice_1kg, 120, currency="KGS"),
            ),
        )
    )

    assert [purchase.offer.id for purchase in plan.purchases] == ["kgs"]


def test_hard_budget_is_respected() -> None:
    rice = item("rice")
    rice_1kg = sku("rice-1kg", rice, 1, "kg")
    plan = build_plan(
        problem(
            demands=(Demand(rice, Quantity("1", "kg")),),
            offers=(offer("rice", rice_1kg, 120),),
            budget=120,
        )
    )

    assert plan.status is PlanStatus.FEASIBLE
    assert plan.total_cost.amount == Decimal("120")
    assert plan.budget_remaining.amount == Decimal("0")


def test_impossible_budget_returns_explicit_infeasible_result() -> None:
    rice = item("rice")
    rice_1kg = sku("rice-1kg", rice, 1, "kg")
    plan = build_plan(
        problem(
            demands=(Demand(rice, Quantity("1", "kg")),),
            offers=(offer("rice", rice_1kg, 120),),
            budget=100,
        )
    )

    assert plan.status is PlanStatus.INFEASIBLE
    assert plan.purchases == ()
    assert plan.minimum_required_cost == Money(120, "KGS")
    assert "exceeds budget" in plan.infeasibility_reasons[0]


def test_missing_market_coverage_returns_explicit_infeasible_result() -> None:
    rice = item("rice")
    plan = build_plan(
        problem(demands=(Demand(rice, Quantity("1", "kg")),))
    )

    assert plan.status is PlanStatus.INFEASIBLE
    assert "rice" in plan.infeasibility_reasons[0]


def test_compatible_units_are_normalized_and_aggregated() -> None:
    rice = item("rice")
    rice_1kg = sku("rice-1kg", rice, 1, "kg")
    plan = build_plan(
        problem(
            demands=(
                Demand(rice, Quantity("0.4", "kg")),
                Demand(rice, Quantity("350", "g")),
            ),
            offers=(offer("rice", rice_1kg, 120),),
        )
    )

    entry = coverage(plan, "rice")
    assert entry.required == Quantity("750", "g")
    assert plan.purchases[0].packs == 1


def test_incompatible_units_for_same_item_are_rejected() -> None:
    strange = item("strange")
    with pytest.raises(ValueError, match="incompatible demand units"):
        build_plan(
            problem(
                demands=(
                    Demand(strange, Quantity("500", "g")),
                    Demand(strange, Quantity("500", "ml")),
                )
            )
        )


def test_total_cost_equals_sum_of_selected_packages() -> None:
    rice = item("rice")
    milk = item("milk")
    rice_800 = sku("rice-800", rice, 800, "g")
    milk_1l = sku("milk-1l", milk, 1, "l")
    plan = build_plan(
        problem(
            demands=(
                Demand(rice, Quantity("900", "g")),
                Demand(milk, Quantity("1500", "ml")),
            ),
            offers=(
                offer("rice", rice_800, 120),
                offer("milk", milk_1l, 90),
            ),
        )
    )

    assert plan.total_cost.amount == Decimal("420")
    assert sum((p.cost.amount for p in plan.purchases), Decimal("0")) == Decimal("420")


def test_same_problem_produces_same_plan() -> None:
    rice = item("rice")
    rice_800 = sku("rice-800", rice, 800, "g")
    p = problem(
        demands=(Demand(rice, Quantity("900", "g")),),
        offers=(offer("rice", rice_800, 120),),
    )

    assert build_plan(p) == build_plan(p)


def test_m1_acceptance_fixture_builds_expected_package_aware_plan() -> None:
    rice = item("rice")
    chicken = item("chicken")
    milk = item("milk")

    rice_a = sku("rice-a-800", rice, 800, "g")
    rice_b = sku("rice-b-1kg", rice, 1, "kg")
    chicken_a = sku("chicken-a-1kg", chicken, 1, "kg")
    chicken_b = sku("chicken-b-500", chicken, 500, "g")
    milk_a = sku("milk-a-1l", milk, 1, "l")
    milk_b = sku("milk-b-930", milk, 930, "ml")

    plan = build_plan(
        problem(
            demands=(
                Demand(rice, Quantity(900, "g")),
                Demand(chicken, Quantity(1, "kg")),
                Demand(milk, Quantity(1500, "ml")),
            ),
            inventory=(InventoryLot("home-rice", rice, Quantity(350, "g")),),
            offers=(
                offer("a-rice", rice_a, 120, seller="store-a"),
                offer("b-rice", rice_b, 135, seller="store-b"),
                offer("a-chicken", chicken_a, 370, seller="store-a"),
                offer("b-chicken", chicken_b, 180, seller="store-b"),
                offer("a-milk", milk_a, 90, seller="store-a"),
                offer("b-milk", milk_b, 82, seller="store-b"),
            ),
        )
    )

    assert plan.status is PlanStatus.FEASIBLE
    assert plan.total_cost == Money(644, "KGS")
    assert [(p.offer.id, p.packs) for p in plan.purchases] == [
        ("b-chicken", 2),
        ("b-milk", 2),
        ("a-rice", 1),
    ]
    assert plan.budget_remaining == Money(2356, "KGS")


def test_mixed_package_combination_can_beat_every_single_sku_strategy() -> None:
    rice = item("rice")
    large = sku("large", rice, 1000, "g")
    small = sku("small", rice, 500, "g")

    plan = build_plan(
        problem(
            demands=(Demand(rice, Quantity("1300", "g")),),
            offers=(
                offer("large", large, 100),
                offer("small", small, 60),
            ),
        )
    )

    # 2 large = 200, 3 small = 180, but 1 large + 1 small = 160.
    assert [(p.offer.id, p.packs) for p in plan.purchases] == [
        ("large", 1),
        ("small", 1),
    ]
    assert plan.total_cost == Money(160, "KGS")
    assert leftover(plan, "rice").quantity == Quantity(200, "g")


def test_planner_is_independent_of_ambient_decimal_context() -> None:
    from decimal import localcontext

    now = datetime(2026, 9, 2, tzinfo=UTC)
    item = Item("bulk", "Bulk")
    sku = SKU(
        "bulk-pack",
        item,
        "Bulk pack",
        Quantity("50000000.000000001", "g"),
    )
    offer = Offer(
        "bulk-offer",
        sku,
        "store",
        Money("123456789.123456789", "KGS"),
        now,
        "fixture",
    )
    problem = PlanningProblem(
        demands=(Demand(item, Quantity("123456789.123456789", "g")),),
        inventory=InventorySnapshot(),
        market=MarketSnapshot(now, (offer,)),
        policy=PlanningPolicy(Money("1000000000", "KGS")),
    )

    plans = []
    for precision in (6, 12, 28, 50):
        with localcontext() as context:
            context.prec = precision
            plans.append(build_plan(problem))

    assert plans == [plans[0]] * 4
    assert plans[0].purchases[0].packs == 3
    assert plans[0].total_cost == Money("370370367.370370367", "KGS")
