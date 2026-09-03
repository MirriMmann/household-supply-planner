from household_supply.market.catalogs.globus_demo_staples import (
    build_globus_demo_staples_catalog,
)
from household_supply.market.providers.globus_online import (
    GlobusOnlineDemoProvider,
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


def test_each_item_uses_one_quantity_dimension() -> None:
    catalog, _ = build_globus_demo_staples_catalog()

    dimensions_by_item: dict[str, set[str]] = {}

    for sku in catalog.skus:
        dimensions_by_item.setdefault(
            sku.item.id,
            set(),
        ).add(sku.package_quantity.dimension)

    assert all(
        len(dimensions) == 1
        for dimensions in dimensions_by_item.values()
    )


def test_catalog_listings_can_configure_m5_provider() -> None:
    _, listings = build_globus_demo_staples_catalog()

    provider = GlobusOnlineDemoProvider(listings=listings)

    assert provider.provider_id == "globus-online-demo"
    assert len(provider.listings) == len(listings)


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


def test_package_metadata_is_explicit_in_sku_name() -> None:
    catalog, _ = build_globus_demo_staples_catalog()

    unit_markers = {
        "g": ("г", "гр"),
        "kg": ("кг",),
        "ml": ("мл",),
        "l": ("л",),
        "piece": ("шт",),
        "pieces": ("шт",),
        "pcs": ("шт",),
    }

    for sku in catalog.skus:
        amount = format(sku.package_quantity.amount, "f")
        if "." in amount:
            amount = amount.rstrip("0").rstrip(".")

        name = (
            sku.name.casefold()
            .replace(" ", "")
            .replace("\u00a0", "")
            .replace("\u202f", "")
        )

        assert any(
            f"{amount}{marker}" in name
            for marker in unit_markers[sku.package_quantity.unit]
        ), sku.id
