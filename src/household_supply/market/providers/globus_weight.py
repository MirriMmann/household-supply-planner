"""Bounded per-kg reference acquisition for explicitly enumerated Globus goods.

No search/crawl, address guessing, checkout min/step, or fake purchasable SKU.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from household_supply.domain.money import Money
from household_supply.domain.quantity import Quantity
from household_supply.domain.weighted import (
    WeightedPriceEstimate, WeightedPriceObservation, estimate_weight_cost,
)
from household_supply.market.catalogs.globus_weight_candidates import WEIGHT_CANDIDATES

from .globus_online import (
    GlobusOnlineFetchError,
    GlobusOnlineListing,
    HttpTextTransport,
    UrllibHttpTextTransport,
    parse_globus_online_weight_product,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class GlobusWeightPriceService:
    """One selected official listing per request, never all 10 by default."""

    transport: HttpTextTransport = field(default_factory=UrllibHttpTextTransport)
    clock: Callable[[], datetime] = _utc_now
    timeout_seconds: float = 10.0

    def estimate(self, *, item_id: str, requested: Quantity) -> WeightedPriceEstimate:
        matches = [row for row in WEIGHT_CANDIDATES if row.item_id == item_id]
        if len(matches) != 1:
            raise ValueError("unknown or ambiguous weighted Item")
        if requested.dimension != "mass" or requested.amount <= 0:
            raise ValueError("requested weighted quantity must be positive mass")
        candidate = matches[0]
        listing = GlobusOnlineListing(candidate.source_url)
        response = self.transport.get(listing.url, timeout_seconds=self.timeout_seconds)
        if response.status != 200:
            raise GlobusOnlineFetchError("weighted Globus listing returned non-200")
        # Reject different identity, path, scheme, host or redirected credentials.
        resolved = GlobusOnlineListing(response.final_url)
        if (
            resolved.external_product_id != listing.external_product_id
            or resolved.seller_id != listing.seller_id
        ):
            raise GlobusOnlineFetchError("weighted Globus listing changed identity")
        parsed = parse_globus_online_weight_product(response.text)
        now = self.clock()
        observation = WeightedPriceObservation(
            item_id=candidate.item_id,
            source_ref=listing.url,
            observed_at=now,
            price=parsed.price_per_kg,
            reference_mass=Quantity("1", "kg"),
            available=parsed.available,
        )
        return WeightedPriceEstimate(
            observation=observation,
            requested=requested,
            estimated_upper_bound=estimate_weight_cost(observation, requested),
        )
