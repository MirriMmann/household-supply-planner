"""Offline M8 vertical slice: household facts -> state -> learning -> recurring demand -> plan."""

from datetime import datetime, timedelta, timezone
from tempfile import TemporaryDirectory

from household_supply import (
    Item,
    MarketSnapshot,
    Money,
    MultiObjectivePolicy,
    Offer,
    PlanningPolicy,
    PlanningProblem,
    Quantity,
    SKU,
    build_multi_objective_plan,
    compile_demand_sources,
)
from household_supply.household import (
    ConsumptionObservation,
    FileHouseholdEventRepository,
    HouseholdEventId,
    HouseholdLearningService,
    InventoryCorrection,
    PurchaseEvent,
)


UTC = timezone.utc
DAY_1 = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)


def main() -> None:
    milk = Item("milk", "Milk", "dairy")
    milk_1l = SKU("milk-1l", milk, "Milk 1 L", Quantity("1", "l"))

    with TemporaryDirectory(prefix="household-m8-") as directory:
        household = HouseholdLearningService(FileHouseholdEventRepository(directory))
        household.record(
            PurchaseEvent(
                event_id=HouseholdEventId("milk-purchase-1"),
                item=milk,
                quantity=Quantity("2", "l"),
                occurred_at=DAY_1,
                recorded_at=DAY_1,
                sku_id=milk_1l.id,
                source_ref="receipt-demo-1",
            )
        )
        household.record(
            ConsumptionObservation(
                event_id=HouseholdEventId("milk-use-day-1"),
                item=milk,
                quantity_consumed=Quantity("400", "ml"),
                period_start=DAY_1,
                period_end=DAY_1 + timedelta(days=1),
                recorded_at=DAY_1 + timedelta(days=1),
                source_ref="manual observation",
            )
        )
        household.record(
            ConsumptionObservation(
                event_id=HouseholdEventId("milk-use-day-2"),
                item=milk,
                quantity_consumed=Quantity("400", "ml"),
                period_start=DAY_1 + timedelta(days=1),
                period_end=DAY_1 + timedelta(days=2),
                recorded_at=DAY_1 + timedelta(days=2),
                source_ref="manual observation",
            )
        )
        household.record(
            InventoryCorrection(
                event_id=HouseholdEventId("milk-count-day-2"),
                item=milk,
                quantity_on_hand=Quantity("1100", "ml"),
                occurred_at=DAY_1 + timedelta(days=2),
                recorded_at=DAY_1 + timedelta(days=2),
                reason="manual fridge count",
            )
        )

        as_of = DAY_1 + timedelta(days=3)
        state = household.state(as_of=as_of)
        estimates = household.estimates(as_of=as_of)
        recurring = household.recurring_need_source(
            source_id="next-seven-days",
            horizon_days="7",
            as_of=as_of,
        )
        demand = compile_demand_sources((recurring,))

        market = MarketSnapshot(
            captured_at=as_of,
            offers=(
                Offer(
                    id="milk-demo-offer",
                    sku=milk_1l,
                    seller_id="demo-store",
                    price=Money("120", "KGS"),
                    observed_at=as_of,
                    source="m8 offline fixture",
                ),
            ),
        )
        problem = PlanningProblem(
            demands=demand.demands,
            inventory=state.inventory_snapshot(),
            market=market,
            policy=PlanningPolicy(Money("1000", "KGS")),
        )
        plan = build_multi_objective_plan(
            problem, MultiObjectivePolicy.zero("KGS")
        )

        estimate = estimates[0]
        print("M8 household state and learning")
        print("  stored events:", len(household.history().events))
        print("  milk on hand:", state.quantity_for("milk"))
        print(
            "  learned daily milk:",
            estimate.daily_quantity,
            f"samples={estimate.sample_count}",
            f"spread={estimate.uncertainty}",
        )
        print("  seven-day recurring demand:", demand.demands[0].quantity)
        print("  plan status:", plan.status.value)
        print("  purchase total:", plan.total_cost)
        for purchase in plan.purchases:
            print(
                f"  purchase: {purchase.offer.sku.name} x {purchase.packs} "
                f"= {purchase.cost}"
            )
        print("  projected leftovers:", plan.projected_leftovers[0].quantity)


if __name__ == "__main__":
    main()
