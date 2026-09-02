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

    def __post_init__(self) -> None:
        offer_ids = [offer.id for offer in self.offers]
        if len(offer_ids) != len(set(offer_ids)):
            raise ValueError("market snapshot contains duplicate offer ids")
        for offer in self.offers:
            try:
                is_future = offer.observed_at > self.captured_at
            except TypeError as exc:
                raise ValueError(
                    "market snapshot and offer timestamps must use compatible timezone awareness"
                ) from exc
            if is_future:
                raise ValueError(
                    f"offer observation is later than market snapshot: {offer.id}"
                )
