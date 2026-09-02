from datetime import UTC, datetime

from household_supply import (
    Demand,
    InventoryLot,
    InventorySnapshot,
    Item,
    MarketSnapshot,
    Money,
    Offer,
    PlanningPolicy,
    PlanningProblem,
    Quantity,
    SKU,
    build_plan,
)


now = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)

rice = Item("rice", "Рис", "food")
chicken = Item("chicken", "Куриное филе", "food")
milk = Item("milk", "Молоко", "food")

rice_a = SKU("rice-a-800", rice, "Рис 800 г", Quantity(800, "g"))
rice_b = SKU("rice-b-1kg", rice, "Рис 1 кг", Quantity(1, "kg"))
chicken_a = SKU("chicken-a-1kg", chicken, "Филе 1 кг", Quantity(1, "kg"))
chicken_b = SKU("chicken-b-500", chicken, "Филе 500 г", Quantity(500, "g"))
milk_a = SKU("milk-a-1l", milk, "Молоко 1 л", Quantity(1, "l"))
milk_b = SKU("milk-b-930", milk, "Молоко 930 мл", Quantity(930, "ml"))


def market_offer(id_: str, sku: SKU, seller: str, price: int) -> Offer:
    return Offer(
        id=id_,
        sku=sku,
        seller_id=seller,
        price=Money(price, "KGS"),
        observed_at=now,
        source="m1-fixture",
    )


problem = PlanningProblem(
    demands=(
        Demand(rice, Quantity(900, "g")),
        Demand(chicken, Quantity(1, "kg")),
        Demand(milk, Quantity(1500, "ml")),
    ),
    inventory=InventorySnapshot(
        (InventoryLot("home-rice", rice, Quantity(350, "g")),)
    ),
    market=MarketSnapshot(
        now,
        (
            market_offer("a-rice", rice_a, "store-a", 120),
            market_offer("b-rice", rice_b, "store-b", 135),
            market_offer("a-chicken", chicken_a, "store-a", 370),
            market_offer("b-chicken", chicken_b, "store-b", 180),
            market_offer("a-milk", milk_a, "store-a", 90),
            market_offer("b-milk", milk_b, "store-b", 82),
        ),
    ),
    policy=PlanningPolicy(Money(3000, "KGS")),
)

plan = build_plan(problem)

print(f"status: {plan.status}")
print(f"total: {plan.total_cost.amount} {plan.total_cost.currency}")
print(f"budget remaining: {plan.budget_remaining.amount} {plan.budget_remaining.currency}")
print("purchases:")
for purchase in plan.purchases:
    print(
        f"  - {purchase.offer.seller_id}: {purchase.offer.sku.name} "
        f"x {purchase.packs} = {purchase.cost.amount} {purchase.cost.currency}"
    )
print("projected leftovers:")
for entry in plan.projected_leftovers:
    print(f"  - {entry.item_id}: {entry.quantity.amount} {entry.quantity.unit}")
