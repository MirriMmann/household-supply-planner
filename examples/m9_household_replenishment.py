"""Offline M9 vertical slice: household history -> durable replenishment plan."""

from datetime import datetime, timedelta, timezone
from tempfile import TemporaryDirectory

from household_supply import (
    CatalogBinding,
    CatalogSnapshot,
    ExternalListingKey,
    Item,
    MarketAcquisitionBatch,
    MarketObservation,
    Money,
    Quantity,
    SKU,
    StaticMarketProvider,
)
from household_supply.application import (
    FilePlanRepository,
    HouseholdReplenishmentRequest,
    HouseholdReplenishmentService,
    PlanApplicationService,
    PlanId,
    PlanLifecycleService,
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
NOW = DAY_1 + timedelta(days=3)


def main() -> None:
    milk = Item("milk", "Milk", "dairy")
    milk_sku = SKU("milk-1l", milk, "Milk 1 L", Quantity("1", "l"))
    key = ExternalListingKey("fixture", "demo-store", "milk-1l")
    catalog = CatalogSnapshot(
        (milk_sku,),
        (CatalogBinding(key, milk_sku.id, "m9 fixture"),),
    )
    market_batch = MarketAcquisitionBatch(
        provider_id="fixture",
        acquired_at=NOW,
        observations=(
            MarketObservation(
                id="milk-observation",
                provider_id="fixture",
                seller_id="demo-store",
                external_product_id="milk-1l",
                price=Money("120", "KGS"),
                observed_at=NOW,
                package_quantity=Quantity("1", "l"),
                source_ref="fixture://milk",
            ),
        ),
    )

    with TemporaryDirectory(prefix="household-m9-") as directory:
        household = HouseholdLearningService(
            FileHouseholdEventRepository(f"{directory}/household")
        )
        household.record(
            PurchaseEvent(
                HouseholdEventId("milk-purchase"),
                milk,
                Quantity("2", "l"),
                DAY_1,
                DAY_1,
                sku_id=milk_sku.id,
            )
        )
        household.record(
            ConsumptionObservation(
                HouseholdEventId("milk-day-1"),
                milk,
                Quantity("400", "ml"),
                DAY_1,
                DAY_1 + timedelta(days=1),
                DAY_1 + timedelta(days=1),
            )
        )
        household.record(
            ConsumptionObservation(
                HouseholdEventId("milk-day-2"),
                milk,
                Quantity("400", "ml"),
                DAY_1 + timedelta(days=1),
                DAY_1 + timedelta(days=2),
                DAY_1 + timedelta(days=2),
            )
        )
        household.record(
            InventoryCorrection(
                HouseholdEventId("milk-count"),
                milk,
                Quantity("1100", "ml"),
                DAY_1 + timedelta(days=2),
                DAY_1 + timedelta(days=2),
                "manual fridge count",
            )
        )

        planner = PlanApplicationService(
            catalog,
            (StaticMarketProvider(market_batch),),
            clock=lambda: NOW,
        )
        plans = PlanLifecycleService(
            planner,
            FilePlanRepository(f"{directory}/plans"),
            clock=lambda: NOW,
            id_factory=lambda: PlanId("m9-demo-plan"),
        )
        replenishment = HouseholdReplenishmentService(
            household,
            plans,
            clock=lambda: NOW,
        )
        result = replenishment.create(
            HouseholdReplenishmentRequest(Money("1000", "KGS"), "7")
        )

        prep = result.preparation
        saved = result.plan_record.result.to_mapping()
        print("M9 household replenishment workflow")
        print("  plan id:", result.plan_record.plan_id)
        print("  household events:", len(prep.history.events))
        print("  milk on hand:", prep.state.quantity_for("milk"))
        print("  learned daily milk:", prep.estimates[0].daily_quantity)
        print("  seven-day demand:", prep.demand_compilation.demands[0].quantity)
        print("  plan status:", saved["status"])
        print("  purchase total:", saved["total_cost"])
        print("  persisted plans:", len(plans.list_recent()))


if __name__ == "__main__":
    main()
