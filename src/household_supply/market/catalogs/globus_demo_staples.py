from __future__ import annotations

from household_supply.domain.catalog import (
    CatalogBinding,
    CatalogSnapshot,
    ExternalListingKey,
)
from household_supply.domain.items import Item, SKU
from household_supply.domain.quantity import Quantity
from household_supply.market.providers.globus_online import (
    GlobusOnlineListing,
)


MILK_ITEM = Item(
    id="milk",
    canonical_name="Молоко",
    category="food",
    aliases=(
        "молоко",
        "milk",
    ),
)


MILK_SKU = SKU(
    id="globus_milk_umut_1l",
    item=MILK_ITEM,
    name="Молоко Умут и К 3,2% 1000г т/п",
    package_quantity=Quantity("1", "l"),
    brand="Умут и Ко",
)


MILK_LISTING = GlobusOnlineListing(
    url=(
        "https://globus-online.kg/ru-kg/good/"
        "3b709086a89e4a1ab6c238ca5cf1a742000100010000"
    )
)


MILK_BINDING = CatalogBinding(
    listing_key=ExternalListingKey(
        provider_id=MILK_LISTING.seller_id,
        seller_id=MILK_LISTING.seller_id,
        external_product_id=MILK_LISTING.external_product_id,
    ),
    sku_id=MILK_SKU.id,
    source=MILK_LISTING.url,
)


def build_globus_demo_staples_catalog() -> tuple[
    CatalogSnapshot,
    tuple[GlobusOnlineListing, ...],
]:
    catalog = CatalogSnapshot(
        skus=(MILK_SKU,),
        bindings=(MILK_BINDING,),
    )

    listings = (MILK_LISTING,)

    return catalog, listings