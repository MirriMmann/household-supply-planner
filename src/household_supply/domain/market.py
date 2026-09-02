from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .catalog import ExternalListingKey
from .items import SKU
from .money import Money


def _require_aware_datetime(value: datetime, *, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class OfferProvenance:
    """Structured lineage for an Offer admitted from external market evidence."""

    observation_id: str
    listing_key: ExternalListingKey
    source_ref: str = ""

    def __post_init__(self) -> None:
        observation_id = self.observation_id.strip()
        if not observation_id:
            raise ValueError("offer provenance observation_id must not be empty")
        object.__setattr__(self, "observation_id", observation_id)
        object.__setattr__(self, "source_ref", self.source_ref.strip())


@dataclass(frozen=True, slots=True)
class Offer:
    id: str
    sku: SKU
    seller_id: str
    price: Money
    observed_at: datetime
    source: str
    available: bool = True
    provenance: OfferProvenance | None = None

    def __post_init__(self) -> None:
        offer_id = self.id.strip()
        seller_id = self.seller_id.strip()
        source = self.source.strip()
        if not offer_id:
            raise ValueError("offer id must not be empty")
        if not seller_id:
            raise ValueError("seller id must not be empty")
        if not source:
            raise ValueError("offer source must not be empty")
        if self.price.amount < 0:
            raise ValueError("offer price must not be negative")
        if not isinstance(self.available, bool):
            raise TypeError("offer available must be bool")
        _require_aware_datetime(self.observed_at, label="offer observed_at")
        if self.provenance is not None:
            if self.provenance.listing_key.provider_id != source:
                raise ValueError("offer source does not match provenance provider_id")
            if self.provenance.listing_key.seller_id != seller_id:
                raise ValueError("offer seller_id does not match provenance seller_id")
        object.__setattr__(self, "id", offer_id)
        object.__setattr__(self, "seller_id", seller_id)
        object.__setattr__(self, "source", source)


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    captured_at: datetime
    offers: tuple[Offer, ...]

    def __post_init__(self) -> None:
        _require_aware_datetime(self.captured_at, label="market snapshot captured_at")
        normalized_offers = tuple(self.offers)
        offer_ids = [offer.id for offer in normalized_offers]
        if len(offer_ids) != len(set(offer_ids)):
            raise ValueError("market snapshot contains duplicate offer ids")
        for offer in normalized_offers:
            if offer.observed_at > self.captured_at:
                raise ValueError(
                    f"offer observation is later than market snapshot: {offer.id}"
                )
        object.__setattr__(self, "offers", normalized_offers)
