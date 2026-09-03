from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from household_supply.domain import CatalogSnapshot

from .globus_online import (
    GlobusOnlineDemoProvider,
    GlobusOnlineListing,
    HttpTextTransport,
)


_GLOBUS_PROVIDER_ID = "globus-online-demo"


@dataclass(frozen=True, slots=True)
class GlobusCatalogProviderFactory:
    """Select exact Globus listings for demanded catalog Items.

    Selection follows existing CatalogBinding identities. Product names are never
    used for matching. The resulting provider fetches only listings that can satisfy
    the current request instead of scanning the complete configured catalog.
    """

    catalog: CatalogSnapshot
    listings: tuple[GlobusOnlineListing, ...]
    transport: HttpTextTransport | None = None
    timeout_seconds: float = 10.0
    clock: Callable[[], datetime] | None = None

    def __post_init__(self) -> None:
        listings = tuple(self.listings)
        if not listings:
            raise ValueError("Globus catalog provider factory requires listings")

        identities = [
            (listing.seller_id, listing.external_product_id) for listing in listings
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("Globus catalog provider factory contains duplicate listings")

        globus_bindings = tuple(
            binding
            for binding in self.catalog.bindings
            if binding.listing_key.provider_id == _GLOBUS_PROVIDER_ID
        )
        globus_binding_keys = {
            (
                binding.listing_key.seller_id,
                binding.listing_key.external_product_id,
            )
            for binding in globus_bindings
        }
        listing_keys = set(identities)
        unbound = sorted(listing_keys - globus_binding_keys)
        if unbound:
            raise ValueError(
                "Globus catalog provider factory contains listings without exact "
                f"catalog bindings: {unbound!r}"
            )
        missing_listings = sorted(globus_binding_keys - listing_keys)
        if missing_listings:
            raise ValueError(
                "Globus catalog provider factory has bindings without configured "
                f"listings: {missing_listings!r}"
            )

        sku_by_id = {sku.id: sku for sku in self.catalog.skus}
        covered_items = {sku_by_id[binding.sku_id].item.id for binding in globus_bindings}
        catalog_items = {sku.item.id for sku in self.catalog.skus}
        uncovered_items = sorted(catalog_items - covered_items)
        if uncovered_items:
            raise ValueError(
                "Globus catalog provider factory has Items without Globus bindings: "
                + ", ".join(uncovered_items)
            )

        if self.timeout_seconds <= 0:
            raise ValueError("Globus catalog provider timeout_seconds must be positive")
        if self.clock is not None and not callable(self.clock):
            raise TypeError("Globus catalog provider clock must be callable")

        object.__setattr__(self, "listings", listings)

    def __call__(self, item_ids: frozenset[str]) -> tuple[GlobusOnlineDemoProvider, ...]:
        normalized_item_ids = frozenset(item_id.strip() for item_id in item_ids)
        if not normalized_item_ids or "" in normalized_item_ids:
            raise ValueError("Globus catalog provider selection requires item ids")

        sku_by_id = {sku.id: sku for sku in self.catalog.skus}
        catalog_item_ids = {sku.item.id for sku in self.catalog.skus}
        unknown = sorted(normalized_item_ids - catalog_item_ids)
        if unknown:
            raise ValueError(f"Globus provider selection references unknown items: {unknown}")

        selected_keys: set[tuple[str, str]] = set()
        covered_items: set[str] = set()
        for binding in self.catalog.bindings:
            if binding.listing_key.provider_id != _GLOBUS_PROVIDER_ID:
                continue
            sku = sku_by_id[binding.sku_id]
            if sku.item.id not in normalized_item_ids:
                continue
            selected_keys.add(
                (
                    binding.listing_key.seller_id,
                    binding.listing_key.external_product_id,
                )
            )
            covered_items.add(sku.item.id)

        missing = sorted(normalized_item_ids - covered_items)
        if missing:
            raise ValueError(
                "Globus catalog has no exact listing bindings for demanded items: "
                + ", ".join(missing)
            )

        selected = tuple(
            listing
            for listing in self.listings
            if (listing.seller_id, listing.external_product_id) in selected_keys
        )
        if not selected:
            raise ValueError("Globus catalog provider selection produced no listings")

        kwargs = {"listings": selected, "timeout_seconds": self.timeout_seconds}
        if self.transport is not None:
            kwargs["transport"] = self.transport
        if self.clock is not None:
            kwargs["clock"] = self.clock
        return (GlobusOnlineDemoProvider(**kwargs),)
