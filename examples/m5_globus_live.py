"""Opt-in live smoke test for the public Globus Online demo catalog.

This example performs network requests and is intentionally not executed by CI.
The public site states that without a delivery address it exposes a demo catalog,
so these observations must not be treated as address-specific store inventory.
"""

from __future__ import annotations

from household_supply import (
    CatalogBinding,
    CatalogSnapshot,
    Demand,
    ExternalListingKey,
    GlobusOnlineDemoProvider,
    GlobusOnlineListing,
    InventorySnapshot,
    Item,
    Money,
    PlanningPolicy,
    PlanningProblem,
    Quantity,
    SKU,
    acquire_market,
    build_plan,
    compile_market_snapshot,
)

MILK_URL = "https://globus-online.kg/ru-kg/good/23df8084d37545f298d8b6dd01955ff2000200010000"
OIL_URL = "https://globus-online.kg/ru-kg/good/faec27b3ccfd4f96afd4bcd0d9acda03000200010001"

milk = Item("milk", "Milk")
oil = Item("oil", "Sunflower oil")
milk_sku = SKU("globus-milk-1l", milk, "Хорошее дело 2.5% 1L", Quantity(1, "l"))
oil_sku = SKU("globus-oil-1l", oil, "Олейна 1L", Quantity(1, "l"))
milk_listing = GlobusOnlineListing(MILK_URL)
oil_listing = GlobusOnlineListing(OIL_URL)

catalog = CatalogSnapshot(
    (milk_sku, oil_sku),
    tuple(
        CatalogBinding(
            ExternalListingKey(
                "globus-online-demo", "globus-online-demo", listing.external_product_id
            ),
            sku.id,
            "manually verified exact Globus product URL",
        )
        for listing, sku in ((milk_listing, milk_sku), (oil_listing, oil_sku))
    ),
)
provider = GlobusOnlineDemoProvider((milk_listing, oil_listing))
batch = acquire_market(provider)
compilation = compile_market_snapshot(catalog, (batch,), captured_at=batch.acquired_at)

print("Live Globus demo observations")
for observation in batch.observations:
    print(
        f"  {observation.name}: price={observation.price}, "
        f"available={observation.available}, source={observation.source_ref}"
    )

problem = PlanningProblem(
    demands=(
        Demand(milk, Quantity(1500, "ml"), "live smoke"),
        Demand(oil, Quantity(500, "ml"), "live smoke"),
    ),
    inventory=InventorySnapshot(()),
    market=compilation.snapshot,
    policy=PlanningPolicy(Money(5000, "KGS")),
)
plan = build_plan(problem)
print("plan:", plan.status.value, "total=", plan.total_cost)
for purchase in plan.purchases:
    print(f"  {purchase.offer.sku.name} x {purchase.packs} = {purchase.cost}")
