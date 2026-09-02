from household_supply.market.catalogs.globus_demo_staples import (
    build_globus_demo_staples_catalog,
)


def test_globus_demo_staples_catalog() -> None:
    catalog, listings = build_globus_demo_staples_catalog()

    assert len(catalog.skus) >= 20
    assert len(catalog.bindings) == len(catalog.skus)
    assert len(listings) == len(catalog.skus)


def test_sku_ids_are_unique() -> None:
    catalog, _ = build_globus_demo_staples_catalog()

    sku_ids = [sku.id for sku in catalog.skus]

    assert len(sku_ids) == len(set(sku_ids))


def test_item_ids_are_unique() -> None:
    catalog, _ = build_globus_demo_staples_catalog()

    item_ids = [sku.item.id for sku in catalog.skus]

    assert len(item_ids) == len(set(item_ids))


def test_bindings_reference_known_skus() -> None:
    catalog, _ = build_globus_demo_staples_catalog()

    sku_ids = {sku.id for sku in catalog.skus}

    assert all(
        binding.sku_id in sku_ids
        for binding in catalog.bindings
    )


def test_external_product_ids_are_unique() -> None:
    _, listings = build_globus_demo_staples_catalog()

    external_ids = [
        listing.external_product_id
        for listing in listings
    ]

    assert len(external_ids) == len(set(external_ids))


def test_package_quantities_are_positive() -> None:
    catalog, _ = build_globus_demo_staples_catalog()

    assert all(
        sku.package_quantity.amount > 0
        for sku in catalog.skus
    )


def test_urls_are_globus_urls() -> None:
    _, listings = build_globus_demo_staples_catalog()

    assert all(
        listing.url.startswith(
            "https://globus-online.kg/"
        )
        for listing in listings
    )