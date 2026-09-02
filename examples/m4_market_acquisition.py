from __future__ import annotations

from datetime import UTC, datetime, timedelta

from household_supply import (
    CatalogBinding,
    CatalogSnapshot,
    Demand,
    ExternalListingKey,
    InventorySnapshot,
    Item,
    MarketAcquisitionBatch,
    MarketCompilationPolicy,
    MarketObservation,
    Money,
    PlanningPolicy,
    PlanningProblem,
    ProductIdentifier,
    Quantity,
    SKU,
    StaticMarketProvider,
    acquire_market,
    build_plan,
    compile_market_snapshot,
)


def main() -> None:
    now = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)

    rice = Item("rice", "Rice")
    milk = Item("milk", "Milk")

    rice_sku = SKU(
        "rice-800",
        rice,
        "Example Rice 800 g",
        Quantity(800, "g"),
    )
    milk_sku = SKU(
        "milk-1l",
        milk,
        "Example Milk 1 L",
        Quantity(1, "l"),
        identifiers=(ProductIdentifier("gtin", "04600000000001"),),
    )

    catalog = CatalogSnapshot(
        skus=(rice_sku, milk_sku),
        bindings=(
            CatalogBinding(
                ExternalListingKey("demo-feed", "store-a", "rice-listing"),
                rice_sku.id,
                "verified-demo-binding",
            ),
        ),
    )

    observations = (
        # Older price for the same external listing: preserved as evidence but
        # superseded when the MarketSnapshot is compiled.
        MarketObservation(
            "rice-old",
            "demo-feed",
            "store-a",
            "rice-listing",
            Money(135, "KGS"),
            now - timedelta(hours=2),
            package_quantity=Quantity(800, "g"),
            name="Example Rice 800g",
            source_ref="demo://store-a/rice/old",
        ),
        MarketObservation(
            "rice-latest",
            "demo-feed",
            "store-a",
            "rice-listing",
            Money(120, "KGS"),
            now - timedelta(minutes=20),
            package_quantity=Quantity(800, "g"),
            name="Example Rice 800g",
            source_ref="demo://store-a/rice/latest",
        ),
        # No seller-specific binding is needed when an exact catalog product
        # identifier resolves the listing.
        MarketObservation(
            "milk-latest",
            "demo-feed",
            "store-b",
            "milk-listing",
            Money(95, "KGS"),
            now - timedelta(minutes=10),
            product_identifier=ProductIdentifier("gtin", "04600000000001"),
            package_quantity=Quantity(1000, "ml"),
            name="Example Milk 1L",
            source_ref="demo://store-b/milk",
        ),
        # Similar-looking free text is not enough to invent SKU identity.
        MarketObservation(
            "unknown",
            "demo-feed",
            "store-c",
            "mystery-rice",
            Money(80, "KGS"),
            now - timedelta(minutes=5),
            package_quantity=Quantity(1, "kg"),
            name="Super Rice 1 kg",
            source_ref="demo://store-c/mystery-rice",
        ),
    )

    provider = StaticMarketProvider(
        MarketAcquisitionBatch("demo-feed", now, observations)
    )
    batch = acquire_market(provider)
    compilation = compile_market_snapshot(
        catalog,
        (batch,),
        captured_at=now,
        policy=MarketCompilationPolicy(max_observation_age=timedelta(hours=6)),
    )

    print("Market compilation")
    for disposition in compilation.dispositions:
        print(
            f"  {disposition.observation.id}: {disposition.status.value}"
            + (f" — {disposition.detail}" if disposition.detail else "")
        )

    problem = PlanningProblem(
        demands=(
            Demand(rice, Quantity(700, "g"), "demo"),
            Demand(milk, Quantity(1, "l"), "demo"),
        ),
        inventory=InventorySnapshot(()),
        market=compilation.snapshot,
        policy=PlanningPolicy(Money(1000, "KGS")),
    )
    plan = build_plan(problem)

    print("\nAccepted market offers")
    for offer in compilation.snapshot.offers:
        assert offer.provenance is not None
        print(
            f"  {offer.seller_id}: {offer.sku.name} = {offer.price.amount} "
            f"{offer.price.currency} ({offer.provenance.observation_id})"
        )

    print("\nProcurement plan")
    for purchase in plan.purchases:
        print(
            f"  {purchase.offer.sku.name} x {purchase.packs}: "
            f"{purchase.cost.amount} {purchase.cost.currency}"
        )
    print(f"Total: {plan.total_cost.amount} {plan.total_cost.currency}")


if __name__ == "__main__":
    main()
