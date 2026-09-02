from __future__ import annotations

from dataclasses import dataclass

from .quantity import Quantity


@dataclass(frozen=True, slots=True, order=True)
class ProductIdentifier:
    """Exact product identity such as GTIN/EAN/UPC.

    The scheme is normalized to lowercase. The value is intentionally not
    case-folded because not every external identifier namespace is numeric.
    """

    scheme: str
    value: str

    def __post_init__(self) -> None:
        scheme = self.scheme.strip().lower()
        value = self.value.strip()
        if not scheme:
            raise ValueError("product identifier scheme must not be empty")
        if not value:
            raise ValueError("product identifier value must not be empty")
        object.__setattr__(self, "scheme", scheme)
        object.__setattr__(self, "value", value)


@dataclass(frozen=True, slots=True)
class Item:
    id: str
    canonical_name: str
    category: str = ""
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        normalized_id = self.id.strip()
        if not normalized_id:
            raise ValueError("item id must not be empty")
        if not self.canonical_name.strip():
            raise ValueError("item canonical_name must not be empty")
        object.__setattr__(self, "id", normalized_id)
        object.__setattr__(self, "aliases", tuple(self.aliases))


@dataclass(frozen=True, slots=True)
class SKU:
    id: str
    item: Item
    name: str
    package_quantity: Quantity
    brand: str = ""
    identifiers: tuple[ProductIdentifier, ...] = ()

    def __post_init__(self) -> None:
        normalized_id = self.id.strip()
        if not normalized_id:
            raise ValueError("sku id must not be empty")
        if not self.name.strip():
            raise ValueError("sku name must not be empty")
        if self.package_quantity.amount <= 0:
            raise ValueError("sku package quantity must be positive")
        identifiers = tuple(self.identifiers)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("sku contains duplicate product identifiers")
        object.__setattr__(self, "id", normalized_id)
        object.__setattr__(self, "identifiers", identifiers)
