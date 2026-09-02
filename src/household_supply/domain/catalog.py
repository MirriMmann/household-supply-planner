from __future__ import annotations

from dataclasses import dataclass

from .items import SKU, ProductIdentifier


@dataclass(frozen=True, slots=True, order=True)
class ExternalListingKey:
    """Stable identity of one seller listing inside one provider namespace."""

    provider_id: str
    seller_id: str
    external_product_id: str

    def __post_init__(self) -> None:
        provider_id = self.provider_id.strip()
        seller_id = self.seller_id.strip()
        external_product_id = self.external_product_id.strip()
        if not provider_id:
            raise ValueError("provider_id must not be empty")
        if not seller_id:
            raise ValueError("seller_id must not be empty")
        if not external_product_id:
            raise ValueError("external_product_id must not be empty")
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "seller_id", seller_id)
        object.__setattr__(self, "external_product_id", external_product_id)


@dataclass(frozen=True, slots=True)
class CatalogBinding:
    """Explicit, attributable mapping from an external listing to a known SKU."""

    listing_key: ExternalListingKey
    sku_id: str
    source: str

    def __post_init__(self) -> None:
        sku_id = self.sku_id.strip()
        source = self.source.strip()
        if not sku_id:
            raise ValueError("catalog binding sku_id must not be empty")
        if not source:
            raise ValueError("catalog binding source must not be empty")
        object.__setattr__(self, "sku_id", sku_id)
        object.__setattr__(self, "source", source)


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    """Immutable canonical SKU catalog used for one resolution operation."""

    skus: tuple[SKU, ...]
    bindings: tuple[CatalogBinding, ...] = ()

    def __post_init__(self) -> None:
        skus = tuple(self.skus)
        bindings = tuple(self.bindings)

        sku_by_id: dict[str, SKU] = {}
        item_by_id = {}
        identifier_owner: dict[ProductIdentifier, str] = {}
        for sku in skus:
            if sku.id in sku_by_id:
                raise ValueError(f"catalog contains duplicate sku id: {sku.id}")
            sku_by_id[sku.id] = sku

            previous_item = item_by_id.get(sku.item.id)
            if previous_item is not None and previous_item != sku.item:
                raise ValueError(
                    f"catalog contains conflicting item identity: {sku.item.id}"
                )
            item_by_id[sku.item.id] = sku.item

            for identifier in sku.identifiers:
                previous_sku = identifier_owner.get(identifier)
                if previous_sku is not None and previous_sku != sku.id:
                    raise ValueError(
                        "catalog product identifier is assigned to multiple SKUs: "
                        f"{identifier.scheme}:{identifier.value}"
                    )
                identifier_owner[identifier] = sku.id

        binding_by_key: dict[ExternalListingKey, CatalogBinding] = {}
        for binding in bindings:
            if binding.sku_id not in sku_by_id:
                raise ValueError(
                    f"catalog binding references unknown sku: {binding.sku_id}"
                )
            if binding.listing_key in binding_by_key:
                raise ValueError(
                    "catalog contains duplicate external listing binding: "
                    f"{binding.listing_key}"
                )
            binding_by_key[binding.listing_key] = binding

        object.__setattr__(self, "skus", skus)
        object.__setattr__(self, "bindings", bindings)
