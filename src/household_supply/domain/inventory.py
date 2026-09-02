from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .items import Item, SKU
from .quantity import Quantity


@dataclass(frozen=True, slots=True)
class InventoryLot:
    id: str
    item: Item
    quantity: Quantity
    sku: SKU | None = None
    acquired_at: datetime | None = None
    opened_at: datetime | None = None
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("inventory lot id must not be empty")
        if self.sku is not None:
            if self.sku.item.id != self.item.id:
                raise ValueError("inventory lot sku must belong to the same item")
            if not self.quantity.compatible_with(self.sku.package_quantity):
                raise ValueError("inventory lot quantity must be compatible with sku package")


@dataclass(frozen=True, slots=True)
class InventorySnapshot:
    lots: tuple[InventoryLot, ...] = ()

    def __post_init__(self) -> None:
        lot_ids = [lot.id for lot in self.lots]
        if len(lot_ids) != len(set(lot_ids)):
            raise ValueError("inventory snapshot contains duplicate lot ids")
