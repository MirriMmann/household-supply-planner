from __future__ import annotations

from dataclasses import dataclass

from .items import Item
from .quantity import Quantity


@dataclass(frozen=True, slots=True)
class Demand:
    item: Item
    quantity: Quantity
    source: str = "explicit"
