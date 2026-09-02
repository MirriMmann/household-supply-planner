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


_PROVIDER_ID = "globus-online-demo"


def _make_product(
    *,
    item_id: str,
    item_name: str,
    category: str,
    sku_id: str,
    sku_name: str,
    package_amount: str,
    package_unit: str,
    brand: str,
    url: str,
) -> tuple[Item, SKU, GlobusOnlineListing, CatalogBinding]:
    item = Item(
        id=item_id,
        canonical_name=item_name,
        category=category,
    )

    sku = SKU(
        id=sku_id,
        item=item,
        name=sku_name,
        package_quantity=Quantity(
            package_amount,
            package_unit,
        ),
        brand=brand,
    )

    listing = GlobusOnlineListing(url=url)

    binding = CatalogBinding(
        listing_key=ExternalListingKey(
            provider_id=_PROVIDER_ID,
            seller_id=listing.seller_id,
            external_product_id=listing.external_product_id,
        ),
        sku_id=sku.id,
        source=listing.url,
    )

    return item, sku, listing, binding


def build_globus_demo_staples_catalog() -> tuple[
    CatalogSnapshot,
    tuple[GlobusOnlineListing, ...],
]:
    ... 