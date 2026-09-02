from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .items import SKU
from .money import Money


@dataclass(frozen=True, slots=True)
class Offer:
    id: str
    sku: SKU
    seller_id: str
    price: Money
    observed_at: datetime
    source: str
    available: bool = True

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("offer id must not be empty")
        if not self.seller_id.strip():
            raise ValueError("seller id must not be empty")
        if not self.source.strip():
            raise ValueError("offer source must not be empty")
        if self.price.amount < 0:
            raise ValueError("offer price must not be negative")


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    captured_at: datetime
    offers: tuple[Offer, ...]
