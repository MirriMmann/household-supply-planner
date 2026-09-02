from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .catalog import ExternalListingKey
from .items import ProductIdentifier
from .money import Money
from .quantity import Quantity


def _require_aware_datetime(value: datetime, *, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class MarketObservation:
    """Attributable provider observation before catalog resolution.

    Human-readable name/brand are evidence for inspection only. M4 never uses
    free text as sufficient identity for automatic SKU resolution.
    """

    id: str
    provider_id: str
    seller_id: str
    external_product_id: str
    price: Money
    observed_at: datetime
    available: bool = True
    product_identifier: ProductIdentifier | None = None
    package_quantity: Quantity | None = None
    name: str = ""
    brand: str = ""
    source_ref: str = ""

    def __post_init__(self) -> None:
        observation_id = self.id.strip()
        provider_id = self.provider_id.strip()
        seller_id = self.seller_id.strip()
        external_product_id = self.external_product_id.strip()
        if not observation_id:
            raise ValueError("market observation id must not be empty")
        if not provider_id:
            raise ValueError("market observation provider_id must not be empty")
        if not seller_id:
            raise ValueError("market observation seller_id must not be empty")
        if not external_product_id:
            raise ValueError("market observation external_product_id must not be empty")
        if self.price.amount < 0:
            raise ValueError("market observation price must not be negative")
        if not isinstance(self.available, bool):
            raise TypeError("market observation available must be bool")
        if self.package_quantity is not None and self.package_quantity.amount <= 0:
            raise ValueError("market observation package_quantity must be positive")
        _require_aware_datetime(self.observed_at, label="market observation observed_at")
        object.__setattr__(self, "id", observation_id)
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "seller_id", seller_id)
        object.__setattr__(self, "external_product_id", external_product_id)
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "brand", self.brand.strip())
        object.__setattr__(self, "source_ref", self.source_ref.strip())

    @property
    def listing_key(self) -> ExternalListingKey:
        return ExternalListingKey(
            provider_id=self.provider_id,
            seller_id=self.seller_id,
            external_product_id=self.external_product_id,
        )


@dataclass(frozen=True, slots=True)
class MarketAcquisitionBatch:
    """Immutable output of one provider acquisition attempt."""

    provider_id: str
    acquired_at: datetime
    observations: tuple[MarketObservation, ...]

    def __post_init__(self) -> None:
        provider_id = self.provider_id.strip()
        if not provider_id:
            raise ValueError("market acquisition provider_id must not be empty")
        _require_aware_datetime(self.acquired_at, label="market acquisition acquired_at")

        observations = tuple(self.observations)
        seen_ids: set[str] = set()
        for observation in observations:
            if observation.provider_id != provider_id:
                raise ValueError(
                    "observation provider_id does not match acquisition batch provider_id"
                )
            if observation.id in seen_ids:
                raise ValueError(
                    f"market acquisition contains duplicate observation id: {observation.id}"
                )
            seen_ids.add(observation.id)
            if observation.observed_at > self.acquired_at:
                raise ValueError(
                    f"observation occurs after acquisition time: {observation.id}"
                )

        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "observations", observations)
