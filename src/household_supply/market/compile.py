from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from hashlib import sha256

from household_supply.domain import (
    CatalogSnapshot,
    MarketAcquisitionBatch,
    MarketObservation,
    MarketSnapshot,
    Offer,
    OfferProvenance,
)

from .resolve import (
    CatalogResolution,
    CatalogResolutionStatus,
    resolve_market_observation,
)


class MarketObservationDispositionStatus(StrEnum):
    ACCEPTED = "accepted"
    SUPERSEDED = "superseded"
    STALE = "stale"
    UNRESOLVED = "unresolved"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class MarketCompilationPolicy:
    max_observation_age: timedelta | None = None

    def __post_init__(self) -> None:
        if self.max_observation_age is not None and self.max_observation_age < timedelta(0):
            raise ValueError("max_observation_age must not be negative")


@dataclass(frozen=True, slots=True)
class MarketObservationDisposition:
    observation: MarketObservation
    status: MarketObservationDispositionStatus
    resolution: CatalogResolution | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        if self.resolution is not None and self.resolution.observation_id != self.observation.id:
            raise ValueError("market disposition resolution refers to another observation")
        if self.status is MarketObservationDispositionStatus.ACCEPTED:
            if (
                self.resolution is None
                or self.resolution.status is not CatalogResolutionStatus.RESOLVED
            ):
                raise ValueError("accepted market disposition requires resolved catalog identity")
        elif self.status is MarketObservationDispositionStatus.UNRESOLVED:
            if (
                self.resolution is None
                or self.resolution.status is not CatalogResolutionStatus.UNRESOLVED
            ):
                raise ValueError("unresolved market disposition requires unresolved resolution")
        elif self.status in (
            MarketObservationDispositionStatus.SUPERSEDED,
            MarketObservationDispositionStatus.STALE,
        ):
            if self.resolution is not None:
                raise ValueError("superseded/stale disposition must not carry catalog resolution")
        elif self.status is MarketObservationDispositionStatus.CONFLICT:
            if (
                self.resolution is not None
                and self.resolution.status is not CatalogResolutionStatus.CONFLICT
            ):
                raise ValueError("conflict disposition carries non-conflict resolution")


@dataclass(frozen=True, slots=True)
class MarketCompilation:
    snapshot: MarketSnapshot
    dispositions: tuple[MarketObservationDisposition, ...]

    def __post_init__(self) -> None:
        dispositions = tuple(self.dispositions)
        seen_refs: set[tuple[str, str]] = set()
        accepted: dict[tuple[str, str], MarketObservationDisposition] = {}
        for disposition in dispositions:
            observation = disposition.observation
            ref = (observation.provider_id, observation.id)
            if ref in seen_refs:
                raise ValueError("market compilation contains duplicate observation disposition")
            seen_refs.add(ref)
            if disposition.status is MarketObservationDispositionStatus.ACCEPTED:
                accepted[ref] = disposition

        represented: set[tuple[str, str]] = set()
        for offer in self.snapshot.offers:
            provenance = offer.provenance
            if provenance is None:
                raise ValueError("compiled market offer lacks structured provenance")
            ref = (provenance.listing_key.provider_id, provenance.observation_id)
            disposition = accepted.get(ref)
            if disposition is None:
                raise ValueError("compiled market offer has no accepted observation disposition")
            observation = disposition.observation
            resolution = disposition.resolution
            assert resolution is not None and resolution.sku is not None
            if ref in represented:
                raise ValueError("accepted observation is represented by multiple offers")
            if offer.sku != resolution.sku:
                raise ValueError("compiled offer SKU does not match catalog resolution")
            if offer.price != observation.price:
                raise ValueError("compiled offer price does not match observation")
            if offer.available is not observation.available:
                raise ValueError("compiled offer availability does not match observation")
            if offer.observed_at != observation.observed_at:
                raise ValueError("compiled offer observed_at does not match observation")
            if offer.source != observation.provider_id or offer.seller_id != observation.seller_id:
                raise ValueError("compiled offer attribution does not match observation")
            if provenance.listing_key != observation.listing_key:
                raise ValueError("compiled offer listing provenance does not match observation")
            represented.add(ref)

        if represented != set(accepted):
            raise ValueError("accepted market observation is missing from compiled snapshot")
        object.__setattr__(self, "dispositions", dispositions)

    @property
    def accepted_observations(self) -> tuple[MarketObservation, ...]:
        return tuple(
            disposition.observation
            for disposition in self.dispositions
            if disposition.status is MarketObservationDispositionStatus.ACCEPTED
        )


def _stable_offer_id(observation: MarketObservation) -> str:
    key = observation.listing_key
    canonical = "\x00".join(
        (key.provider_id, key.seller_id, key.external_product_id)
    ).encode("utf-8")
    return "market-" + sha256(canonical).hexdigest()[:24]


def _ensure_aware(value: datetime, *, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def compile_market_snapshot(
    catalog: CatalogSnapshot,
    batches: tuple[MarketAcquisitionBatch, ...] | list[MarketAcquisitionBatch],
    *,
    captured_at: datetime,
    policy: MarketCompilationPolicy | None = None,
) -> MarketCompilation:
    """Compile attributable provider observations into a canonical market snapshot.

    For each exact external listing key, only the latest observation is eligible.
    Ties at the same latest timestamp are rejected as conflicts instead of being
    resolved by arbitrary input order.
    """

    _ensure_aware(captured_at, label="market compilation captured_at")
    effective_policy = policy or MarketCompilationPolicy()
    normalized_batches = tuple(batches)

    observations: list[MarketObservation] = []
    seen_observation_refs: set[tuple[str, str]] = set()
    for batch in normalized_batches:
        if batch.acquired_at > captured_at:
            raise ValueError(
                "market acquisition batch was acquired after requested snapshot time"
            )
        for observation in batch.observations:
            ref = (batch.provider_id, observation.id)
            if ref in seen_observation_refs:
                raise ValueError(
                    "duplicate provider observation identity across acquisition batches: "
                    f"{batch.provider_id}:{observation.id}"
                )
            seen_observation_refs.add(ref)
            observations.append(observation)

    grouped: dict[object, list[MarketObservation]] = {}
    for observation in observations:
        grouped.setdefault(observation.listing_key, []).append(observation)

    offers: list[Offer] = []
    dispositions: list[MarketObservationDisposition] = []

    for listing_key in sorted(grouped):
        listing_observations = sorted(
            grouped[listing_key],
            key=lambda observation: (observation.observed_at, observation.id),
        )
        latest_time = listing_observations[-1].observed_at
        latest = [
            observation
            for observation in listing_observations
            if observation.observed_at == latest_time
        ]
        older = [
            observation
            for observation in listing_observations
            if observation.observed_at < latest_time
        ]

        for observation in older:
            dispositions.append(
                MarketObservationDisposition(
                    observation=observation,
                    status=MarketObservationDispositionStatus.SUPERSEDED,
                    detail=f"superseded by observation at {latest_time.isoformat()}",
                )
            )

        if len(latest) != 1:
            for observation in latest:
                dispositions.append(
                    MarketObservationDisposition(
                        observation=observation,
                        status=MarketObservationDispositionStatus.CONFLICT,
                        detail=(
                            "multiple latest observations exist for the same listing "
                            "at the same observed_at timestamp"
                        ),
                    )
                )
            continue

        observation = latest[0]
        if (
            effective_policy.max_observation_age is not None
            and captured_at - observation.observed_at
            > effective_policy.max_observation_age
        ):
            dispositions.append(
                MarketObservationDisposition(
                    observation=observation,
                    status=MarketObservationDispositionStatus.STALE,
                    detail="latest observation exceeds configured maximum age",
                )
            )
            continue

        resolution = resolve_market_observation(catalog, observation)
        if resolution.status is CatalogResolutionStatus.UNRESOLVED:
            dispositions.append(
                MarketObservationDisposition(
                    observation=observation,
                    status=MarketObservationDispositionStatus.UNRESOLVED,
                    resolution=resolution,
                    detail=resolution.detail,
                )
            )
            continue
        if resolution.status is CatalogResolutionStatus.CONFLICT:
            dispositions.append(
                MarketObservationDisposition(
                    observation=observation,
                    status=MarketObservationDispositionStatus.CONFLICT,
                    resolution=resolution,
                    detail=resolution.detail,
                )
            )
            continue

        assert resolution.sku is not None
        offer = Offer(
            id=_stable_offer_id(observation),
            sku=resolution.sku,
            seller_id=observation.seller_id,
            price=observation.price,
            observed_at=observation.observed_at,
            source=observation.provider_id,
            available=observation.available,
            provenance=OfferProvenance(
                observation_id=observation.id,
                listing_key=observation.listing_key,
                source_ref=observation.source_ref,
            ),
        )
        offers.append(offer)
        dispositions.append(
            MarketObservationDisposition(
                observation=observation,
                status=MarketObservationDispositionStatus.ACCEPTED,
                resolution=resolution,
                detail=resolution.detail,
            )
        )

    offers.sort(key=lambda offer: offer.id)
    dispositions.sort(
        key=lambda disposition: (
            disposition.observation.provider_id,
            disposition.observation.seller_id,
            disposition.observation.external_product_id,
            disposition.observation.observed_at,
            disposition.observation.id,
        )
    )
    return MarketCompilation(
        snapshot=MarketSnapshot(captured_at=captured_at, offers=tuple(offers)),
        dispositions=tuple(dispositions),
    )
