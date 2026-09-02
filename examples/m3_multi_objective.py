from datetime import UTC, datetime

from household_supply import (
    Demand,
    InventorySnapshot,
    Item,
    MarketSnapshot,
    Money,
    MultiObjectivePolicy,
    Offer,
    PlanningPolicy,
    PlanningProblem,
    Quantity,
    SKU,
    SurplusPenaltyRate,
    build_multi_objective_plan,
    build_plan,
)


now = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
rice = Item("rice", "Rice")
milk = Item("milk", "Milk")

rice_exact = SKU("rice-600", rice, "Rice 600g", Quantity(600, "g"))
rice_large = SKU("rice-1kg", rice, "Rice 1kg", Quantity(1, "kg"))
milk_a = SKU("milk-a-1l", milk, "Milk 1L A", Quantity(1, "l"))
milk_c = SKU("milk-c-1l", milk, "Milk 1L C", Quantity(1, "l"))

problem = PlanningProblem(
    demands=(
        Demand(rice, Quantity(600, "g")),
        Demand(milk, Quantity(1, "l")),
    ),
    inventory=InventorySnapshot(),
    market=MarketSnapshot(
        captured_at=now,
        offers=(
            Offer("rice-a", rice_exact, "store-a", Money(110, "KGS"), now, "fixture"),
            Offer("rice-b", rice_large, "store-b", Money(100, "KGS"), now, "fixture"),
            Offer("milk-a", milk_a, "store-a", Money(100, "KGS"), now, "fixture"),
            Offer("milk-c", milk_c, "store-c", Money(80, "KGS"), now, "fixture"),
        ),
    ),
    policy=PlanningPolicy(budget=Money(1000, "KGS")),
)

objective_policy = MultiObjectivePolicy(
    additional_store_penalty=Money(100, "KGS"),
    surplus_penalties=(
        SurplusPenaltyRate("rice", Money("0.05", "KGS")),
    ),
)

baseline = build_plan(problem)
optimized = build_multi_objective_plan(problem, objective_policy)

print("Cost-only baseline")
print(f"  purchase cost: {baseline.total_cost.amount} KGS")
print(
    "  sellers: "
    + ", ".join(sorted({purchase.offer.seller_id for purchase in baseline.purchases}))
)
for purchase in baseline.purchases:
    print(f"  - {purchase.offer.id} x {purchase.packs}")

print("\nMulti-objective plan")
print(f"  purchase cost: {optimized.total_cost.amount} KGS")
assert optimized.objective_breakdown is not None
breakdown = optimized.objective_breakdown
print(f"  surplus penalty: {breakdown.surplus_penalty.amount} KGS")
print(f"  additional-store penalty: {breakdown.additional_store_penalty.amount} KGS")
print(f"  objective score: {breakdown.total_score.amount} KGS")
print("  sellers: " + (", ".join(breakdown.selected_sellers) or "none"))
for purchase in optimized.purchases:
    print(f"  - {purchase.offer.id} x {purchase.packs}")

print("\nWhy")
for line in optimized.explanation[:7]:
    print(f"  {line}")
