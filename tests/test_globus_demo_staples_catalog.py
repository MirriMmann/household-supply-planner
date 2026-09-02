from household_supply.market.catalogs.globus_demo_staples import (
    MILK_BINDING,
    MILK_ITEM,
    MILK_LISTING,
    MILK_SKU,
    build_globus_demo_staples_catalog,
)


def test_milk_item() -> None:
    assert MILK_ITEM.id == "milk"
    assert MILK_ITEM.canonical_name == "Молоко"


def test_milk_sku() -> None:
    assert MILK_SKU.id == "globus_milk_umut_1l"
    assert MILK_SKU.item == MILK_ITEM
    assert MILK_SKU.package_quantity.amount == 1
    assert MILK_SKU.package_quantity.unit == "l"
    assert MILK_SKU.brand == "Умут и Ко"


def test_milk_listing_identity() -> None:
    assert MILK_LISTING.seller_id == "globus-online-demo"
    assert (
        MILK_LISTING.external_product_id
        == "3b709086a89e4a1ab6c238ca5cf1a742000100010000"
    )


def test_milk_binding() -> None:
    assert MILK_BINDING.sku_id == MILK_SKU.id
    assert MILK_BINDING.listing_key.provider_id == "globus-online-demo"
    assert MILK_BINDING.listing_key.seller_id == "globus-online-demo"
    assert (
        MILK_BINDING.listing_key.external_product_id
        == "3b709086a89e4a1ab6c238ca5cf1a742000100010000"
    )


def test_build_catalog() -> None:
    catalog, listings = build_globus_demo_staples_catalog()

    assert len(catalog.skus) == 1
    assert len(catalog.bindings) == 1
    assert len(listings) == 1

    assert catalog.skus[0] == MILK_SKU
    assert catalog.bindings[0] == MILK_BINDING
    assert listings[0] == MILK_LISTING