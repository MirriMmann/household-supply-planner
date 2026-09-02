from __future__ import annotations

from dataclasses import dataclass

from .quantity import Quantity


@dataclass(frozen=True, slots=True)
class Item:
    id: str
    canonical_name: str
    category: str = ""
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("item id must not be empty")
        if not self.canonical_name.strip():
            raise ValueError("item canonical_name must not be empty")
        object.__setattr__(self, "aliases", tuple(self.aliases))


@dataclass(frozen=True, slots=True)
class SKU:
    id: str
    item: Item
    name: str
    package_quantity: Quantity
    brand: str = ""

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("sku id must not be empty")
        if not self.name.strip():
            raise ValueError("sku name must not be empty")
        if self.package_quantity.amount <= 0:
            raise ValueError("sku package quantity must be positive")
