from __future__ import annotations

from datetime import datetime, timezone

import pytest

from household_supply.application import (
    ApplicationPlanRequest,
    DemandScopedPlanApplicationService,
    InMemoryPlanRepository,
    PlanId,
    PlanLifecycleService,
    RequestedItem,
    UnknownCatalogItemError,
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
from household_supply.market import GlobusCatalogProviderFactory, StaticMarketProvider
from household_supply.market.providers import GlobusOnlineListing


NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def _fixture_catalog() -> CatalogSnapshot:
    milk = Item("milk", "Молоко", "dairy")
    oil = Item("oil", "Масло", "oil")
    milk_sku = SKU("milk-1l", milk, "Молоко 1л", Quantity(1, "l"))
    oil_sku = SKU("oil-1l", oil, "Масло 1л", Quantity(1, "l"))
    return CatalogSnapshot(
        (milk_sku, oil_sku),
        (
            CatalogBinding(
                ExternalListingKey("fixture", "store", "milk-1l"),
                milk_sku.id,
                "fixture",
            ),
            CatalogBinding(
                ExternalListingKey("fixture", "store", "oil-1l"),
                oil_sku.id,
                "fixture",
            ),
        ),
    )


def _provider_for(item_ids: frozenset[str]) -> tuple[StaticMarketProvider, ...]:
    observations = []
    if "milk" in item_ids:
        observations.append(
            MarketObservation(
                id="milk-observation",
                provider_id="fixture",
                seller_id="store",
                external_product_id="milk-1l",
                price=Money(120, "KGS"),
                observed_at=NOW,
                package_quantity=Quantity(1, "l"),
                source_ref="fixture://milk",
            )
        )
    if "oil" in item_ids:
        observations.append(
            MarketObservation(
                id="oil-observation",
                provider_id="fixture",
                seller_id="store",
                external_product_id="oil-1l",
                price=Money(190, "KGS"),
                observed_at=NOW,
                package_quantity=Quantity(1, "l"),
                source_ref="fixture://oil",
            )
        )
    return (
        StaticMarketProvider(
            MarketAcquisitionBatch("fixture", NOW, tuple(observations))
        ),
    )


def test_demand_scoped_service_acquires_only_demanded_items() -> None:
    seen: list[frozenset[str]] = []

    def factory(item_ids: frozenset[str]):
        seen.append(item_ids)
        return _provider_for(item_ids)

    service = DemandScopedPlanApplicationService(
        _fixture_catalog(), factory, clock=lambda: NOW
    )
    result = service.plan(
        ApplicationPlanRequest(
            demands=(RequestedItem("milk", Quantity(1, "l")),),
            budget=Money(1000, "KGS"),
        )
    )

    assert seen == [frozenset({"milk"})]
    assert [offer.sku.id for offer in result.market_compilation.snapshot.offers] == [
        "milk-1l"
    ]
    assert result.plan.total_cost == Money(120, "KGS")


def test_demand_scoped_service_catalog_preflight_runs_before_factory() -> None:
    called = False

    def factory(_item_ids: frozenset[str]):
        nonlocal called
        called = True
        return _provider_for(frozenset({"milk"}))

    service = DemandScopedPlanApplicationService(
        _fixture_catalog(), factory, clock=lambda: NOW
    )
    request = ApplicationPlanRequest(
        demands=(RequestedItem("unknown", Quantity(1, "piece")),),
        budget=Money(1000, "KGS"),
    )

    with pytest.raises(UnknownCatalogItemError):
        service.plan(request)
    assert called is False


def _globus_fixture() -> tuple[
    CatalogSnapshot,
    tuple[GlobusOnlineListing, ...],
]:
    milk = Item("milk", "Молоко", "dairy")
    oil = Item("oil", "Масло", "oil")
    milk_a = SKU("milk-a", milk, "Молоко A 1л", Quantity(1, "l"))
    milk_b = SKU("milk-b", milk, "Молоко B 1л", Quantity(1, "l"))
    oil_a = SKU("oil-a", oil, "Масло A 1л", Quantity(1, "l"))
    listings = (
        GlobusOnlineListing(
            "https://globus-online.kg/ru-kg/good/aaaaaaaaaaaaaaaa"
        ),
        GlobusOnlineListing(
            "https://globus-online.kg/ru-kg/good/bbbbbbbbbbbbbbbb"
        ),
        GlobusOnlineListing(
            "https://globus-online.kg/ru-kg/good/cccccccccccccccc"
        ),
    )
    catalog = CatalogSnapshot(
        (milk_a, milk_b, oil_a),
        tuple(
            CatalogBinding(
                ExternalListingKey(
                    "globus-online-demo",
                    listing.seller_id,
                    listing.external_product_id,
                ),
                sku.id,
                listing.url,
            )
            for sku, listing in zip((milk_a, milk_b, oil_a), listings, strict=True)
        ),
    )
    return catalog, listings


def test_globus_catalog_provider_factory_selects_exact_item_listings() -> None:
    catalog, listings = _globus_fixture()
    factory = GlobusCatalogProviderFactory(catalog, listings)

    (provider,) = factory(frozenset({"milk"}))

    assert [listing.external_product_id for listing in provider.listings] == [
        listings[0].external_product_id,
        listings[1].external_product_id,
    ]


def test_globus_catalog_provider_factory_rejects_unbound_listing() -> None:
    catalog, listings = _globus_fixture()
    extra = GlobusOnlineListing(
        "https://globus-online.kg/ru-kg/good/dddddddddddddddd"
    )

    with pytest.raises(ValueError, match="without exact catalog bindings"):
        GlobusCatalogProviderFactory(catalog, listings + (extra,))


def test_demand_scoped_service_is_a_lifecycle_planner() -> None:
    service = DemandScopedPlanApplicationService(
        _fixture_catalog(), _provider_for, clock=lambda: NOW
    )
    lifecycle = PlanLifecycleService(
        service,
        InMemoryPlanRepository(),
        clock=lambda: NOW,
        id_factory=lambda: PlanId("scoped-plan"),
    )

    record = lifecycle.create(
        ApplicationPlanRequest(
            demands=(RequestedItem("milk", Quantity(1, "l")),),
            budget=Money(1000, "KGS"),
        )
    )

    assert record.plan_id == PlanId("scoped-plan")
    assert record.result.to_mapping()["total_cost"] == {
        "amount": "120",
        "currency": "KGS",
    }


def test_globus_catalog_provider_factory_rejects_binding_without_listing() -> None:
    catalog, listings = _globus_fixture()

    with pytest.raises(ValueError, match="bindings without configured listings"):
        GlobusCatalogProviderFactory(catalog, listings[:-1])
