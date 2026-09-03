from __future__ import annotations

from datetime import datetime, timedelta, timezone

from household_supply.application import (
    HouseholdOperationsService,
    HouseholdReplenishmentRequest,
    HouseholdReplenishmentService,
    InMemoryPlanRepository,
    PlanApplicationService,
    PlanId,
    PlanLifecycleService,
    PurchaseConfirmationCommand,
    RequestedItem,
    StocktakeCommand,
)
from household_supply.domain import (
    CatalogBinding,
    CatalogSnapshot,
    ExternalListingKey,
    Item,
    MarketAcquisitionBatch,
    MarketObservation,
    Money,
    Quantity,
    SKU,
)
from household_supply.household import (
    HouseholdEventId,
    HouseholdLearningService,
    InMemoryHouseholdEventRepository,
)


UTC = timezone.utc
BASE = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class CurrentFixtureProvider:
    provider_id = "fixture"

    def __init__(self, clock: MutableClock, sku: SKU) -> None:
        self.clock = clock
        self.sku = sku

    def acquire(self) -> MarketAcquisitionBatch:
        observed_at = self.clock()
        return MarketAcquisitionBatch(
            provider_id=self.provider_id,
            acquired_at=observed_at,
            observations=(
                MarketObservation(
                    id=f"milk-{observed_at.isoformat()}",
                    provider_id=self.provider_id,
                    seller_id="store-a",
                    external_product_id=self.sku.id,
                    price=Money("120", "KGS"),
                    observed_at=observed_at,
                    package_quantity=self.sku.package_quantity,
                    source_ref="fixture://milk",
                ),
            ),
        )


def main() -> None:
    clock = MutableClock(BASE)
    milk = Item("milk", "Milk", "dairy")
    milk_sku = SKU("milk-1l", milk, "Milk 1 L", Quantity(1, "l"))
    catalog = CatalogSnapshot(
        (milk_sku,),
        (
            CatalogBinding(
                ExternalListingKey("fixture", "store-a", milk_sku.id),
                milk_sku.id,
                "fixture",
            ),
        ),
    )

    household_repo = InMemoryHouseholdEventRepository()
    household = HouseholdLearningService(household_repo)
    plan_repo = InMemoryPlanRepository()
    planner = PlanApplicationService(
        catalog,
        (CurrentFixtureProvider(clock, milk_sku),),
        clock=clock,
    )
    plan_ids = iter(("m10-first-plan", "m10-next-plan"))
    lifecycle = PlanLifecycleService(
        planner,
        plan_repo,
        clock=clock,
        id_factory=lambda: PlanId(next(plan_ids)),
    )
    replenishment = HouseholdReplenishmentService(household, lifecycle, clock=clock)
    operations = HouseholdOperationsService(household, catalog, plan_repo, clock=clock)

    # Initial stocktake: the system knows what is physically at home.
    operations.record_stocktake(
        StocktakeCommand(
            HouseholdEventId("stocktake-start"),
            "milk",
            Quantity("2", "l"),
        )
    )

    # A first explicit plan needs 3 L total while 2 L is already at home.
    first_plan = replenishment.create(
        HouseholdReplenishmentRequest(
            Money("1000", "KGS"),
            1,
            (RequestedItem("milk", Quantity("3", "l")),),
        )
    ).plan_record

    # The recommendation is not reality until the purchase is confirmed.
    clock.value = BASE + timedelta(days=1)
    confirmed = operations.record_purchase(
        PurchaseConfirmationCommand(
            HouseholdEventId("purchase-confirmed"),
            "milk-1l",
            1,
        ),
        plan_id=first_plan.plan_id,
    )

    # A later stocktake closes the depletion interval.
    clock.value = BASE + timedelta(days=2)
    operations.record_stocktake(
        StocktakeCommand(
            HouseholdEventId("stocktake-end"),
            "milk",
            Quantity("1.2", "l"),
        )
    )

    report = next(
        report
        for report in operations.depletion_reports(as_of=clock.value)
        if report.item.id == "milk"
    )
    assert report.estimate is not None

    # 2.0 L + 1.0 L confirmed purchase - 1.2 L = 1.8 L depleted / 2 days.
    next_plan = replenishment.create(
        HouseholdReplenishmentRequest(Money("1000", "KGS"), 7)
    ).plan_record
    result = next_plan.result.to_mapping()

    print("M10 closed-loop household operations")
    print(f"  initial stock: 2 L")
    print(
        "  first plan purchase: "
        f"{first_plan.result.to_mapping()['purchases'][0]['packs']} x 1 L"
    )
    print(
        "  confirmed purchase: "
        f"actual={confirmed.actual_packs} planned={confirmed.planned_packs}"
    )
    print("  later stock: 1.2 L")
    print(f"  inferred depletion: {report.windows[0].inferred_depletion}")
    print(f"  learned daily depletion: {report.estimate.daily_quantity}")
    print(f"  next 7-day demand: {next_plan.request.to_mapping()['demands'][0]['quantity']}")
    print(
        "  next purchase: "
        f"{result['purchases'][0]['packs']} x 1 L = {result['total_cost']}"
    )
    print(f"  stored household events: {len(household.history().events)}")
    print(f"  stored plans: {len(plan_repo.list_recent(10))}")


if __name__ == "__main__":
    main()
