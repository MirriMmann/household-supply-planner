"""Opt-in M6 live smoke: application request -> live Globus -> procurement plan."""

from __future__ import annotations

from household_supply import (
    ApplicationPlanRequest,
    CatalogBinding,
    CatalogSnapshot,
    ExternalListingKey,
    GlobusOnlineDemoProvider,
    GlobusOnlineListing,
    Item,
    Money,
    PlanApplicationService,
    Quantity,
    RequestedItem,
    SKU,
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
service = PlanApplicationService(
    catalog,
    (GlobusOnlineDemoProvider((milk_listing, oil_listing)),),
)
result = service.plan(
    ApplicationPlanRequest(
        demands=(
            RequestedItem("milk", Quantity(1500, "ml")),
            RequestedItem("oil", Quantity(500, "ml")),
        ),
        budget=Money(5000, "KGS"),
    )
)

print("M6 live application plan")
print("  status:", result.plan.status.value)
print("  market captured:", result.market_compilation.snapshot.captured_at.isoformat())
for offer in result.market_compilation.snapshot.offers:
    print(f"  offer: {offer.sku.name}: {offer.price}")
print("  total:", result.plan.total_cost)
for purchase in result.plan.purchases:
    print(f"  purchase: {purchase.offer.sku.name} x {purchase.packs} = {purchase.cost}")
