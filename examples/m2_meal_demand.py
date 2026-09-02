from __future__ import annotations

from datetime import UTC, datetime

from household_supply import (
    ExplicitNeed,
    ExplicitNeedSource,
    InventoryLot,
    InventorySnapshot,
    Item,
    MarketSnapshot,
    MealDemandSource,
    MealRequest,
    Money,
    Offer,
    PlanningPolicy,
    PlanningProblem,
    Quantity,
    Recipe,
    RecipeIngredient,
    SKU,
    build_plan,
    compile_demand_sources,
)


def main() -> None:
    now = datetime(2026, 9, 2, tzinfo=UTC)

    rice = Item("rice", "Rice")
    chicken = Item("chicken", "Chicken breast")
    oats = Item("oats", "Oats")
    milk = Item("milk", "Milk")

    chicken_rice = Recipe(
        "chicken-rice",
        "Chicken with rice",
        2,
        (
            RecipeIngredient(rice, Quantity(300, "g")),
            RecipeIngredient(chicken, Quantity(400, "g")),
        ),
    )
    oatmeal = Recipe(
        "oatmeal",
        "Oatmeal",
        1,
        (
            RecipeIngredient(oats, Quantity(80, "g")),
            RecipeIngredient(milk, Quantity(250, "ml")),
        ),
    )

    meals = MealDemandSource(
        "week-meals",
        (
            MealRequest(chicken_rice, 4),
            MealRequest(oatmeal, 3),
        ),
    )
    explicit = ExplicitNeedSource(
        "extra-milk",
        (ExplicitNeed(milk, Quantity(250, "ml")),),
    )
    compilation = compile_demand_sources((meals, explicit))

    inventory = InventorySnapshot(
        (
            InventoryLot("rice-home", rice, Quantity(200, "g")),
            InventoryLot("milk-home", milk, Quantity(300, "ml")),
        )
    )

    rice_sku = SKU("rice-500g", rice, "Rice 500g", Quantity(500, "g"))
    chicken_sku = SKU(
        "chicken-500g", chicken, "Chicken 500g", Quantity(500, "g")
    )
    oats_sku = SKU("oats-500g", oats, "Oats 500g", Quantity(500, "g"))
    milk_sku = SKU("milk-1l", milk, "Milk 1L", Quantity(1, "l"))

    market = MarketSnapshot(
        captured_at=now,
        offers=(
            Offer("rice-a", rice_sku, "store-a", Money(70, "KGS"), now, "fixture"),
            Offer(
                "chicken-a",
                chicken_sku,
                "store-a",
                Money(190, "KGS"),
                now,
                "fixture",
            ),
            Offer("oats-a", oats_sku, "store-a", Money(90, "KGS"), now, "fixture"),
            Offer("milk-a", milk_sku, "store-a", Money(95, "KGS"), now, "fixture"),
        ),
    )

    problem = PlanningProblem(
        demands=compilation.demands,
        inventory=inventory,
        market=market,
        policy=PlanningPolicy(Money(3000, "KGS")),
    )
    plan = build_plan(problem)

    print("Compiled demand")
    for demand in compilation.demands:
        print(f"  {demand.item.canonical_name}: {demand.quantity.amount} {demand.quantity.unit}")

    print("\nPurchases")
    for purchase in plan.purchases:
        print(
            f"  {purchase.offer.sku.name} x {purchase.packs}: "
            f"{purchase.cost.amount} {purchase.cost.currency}"
        )

    print(f"\nTotal: {plan.total_cost.amount} {plan.total_cost.currency}")
    print(
        f"Budget remaining: {plan.budget_remaining.amount} "
        f"{plan.budget_remaining.currency}"
    )


if __name__ == "__main__":
    main()
