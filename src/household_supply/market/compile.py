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
    UNAVAILABLE = "unavailable"
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
            if self.observation.price is None:
                raise ValueError("accepted market disposition requires priced observation")
        elif self.status is MarketObservationDispositionStatus.UNAVAILABLE:
            if self.observation.available:
                raise ValueError("unavailable disposition requires unavailable observation")
            if (
                self.resolution is None
                or self.resolution.status is not CatalogResolutionStatus.RESOLVED
            ):
                raise ValueError("unavailable market disposition requires resolved catalog identity")
            if self.observation.price is not None:
                raise ValueError("unavailable disposition is reserved for observations without price")
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
        object.__setattr__(self, "detail", self.detail.strip())


def _stable_offer_id(observation: MarketObservation) -> str:
    key = observation.listing_key
    canonical = "\x00".join(
        (key.provider_id, key.seller_id, key.external_product_id)
    ).encode("utf-8")
    return "market-" + sha256(canonical).hexdigest()[:24]


def _ensure_aware(value: datetime, *, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _normalize_batches(
    batches: tuple[MarketAcquisitionBatch, ...] | list[MarketAcquisitionBatch],
    *,
    captured_at: datetime,
) -> tuple[MarketAcquisitionBatch, ...]:
    canonical_batches: list[MarketAcquisitionBatch] = []
    seen_observation_refs: set[tuple[str, str]] = set()
    for batch in tuple(batches):
        if batch.acquired_at > captured_at:
            raise ValueError(
                "market acquisition batch was acquired after requested snapshot time"
            )
        observations = tuple(
            sorted(
                batch.observations,
                key=lambda observation: (
                    observation.provider_id,
                    observation.seller_id,
                    observation.external_product_id,
                    observation.observed_at,
                    observation.id,
                ),
            )
        )
        for observation in observations:
            ref = (batch.provider_id, observation.id)
            if ref in seen_observation_refs:
                raise ValueError(
                    "duplicate provider observation identity across acquisition batches: "
                    f"{batch.provider_id}:{observation.id}"
                )
            seen_observation_refs.add(ref)
        canonical_batches.append(
            MarketAcquisitionBatch(batch.provider_id, batch.acquired_at, observations)
        )
    canonical_batches.sort(
        key=lambda batch: (
            batch.provider_id,
            batch.acquired_at,
            tuple(
                (
                    observation.seller_id,
                    observation.external_product_id,
                    observation.observed_at,
                    observation.id,
                )
                for observation in batch.observations
            ),
        )
    )
    return tuple(canonical_batches)


def _compile_market_data(
    catalog: CatalogSnapshot,
    batches: tuple[MarketAcquisitionBatch, ...] | list[MarketAcquisitionBatch],
    *,
    captured_at: datetime,
    policy: MarketCompilationPolicy,
) -> tuple[MarketSnapshot, tuple[MarketObservationDisposition, ...]]:
    normalized_batches = _normalize_batches(batches, captured_at=captured_at)
    observations = [
        observation
        for batch in normalized_batches
        for observation in batch.observations
    ]

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
            policy.max_observation_age is not None
            and captured_at - observation.observed_at > policy.max_observation_age
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
        if not observation.available and observation.price is None:
            dispositions.append(
                MarketObservationDisposition(
                    observation=observation,
                    status=MarketObservationDispositionStatus.UNAVAILABLE,
                    resolution=resolution,
                    detail="latest resolved listing is unavailable and exposes no price",
                )
            )
            continue

        assert observation.price is not None
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
    return (
        MarketSnapshot(captured_at=captured_at, offers=tuple(offers)),
        tuple(dispositions),
    )


@dataclass(frozen=True, slots=True)
class MarketCompilation:
    """Self-contained proof record for one market snapshot compilation.

    The exact catalog, acquisition batches, and policy are retained so a
    manually constructed record cannot forge catalog resolution, temporal
    latest-selection, provenance, or planner-facing Offer identity.
    """

    catalog: CatalogSnapshot
    batches: tuple[MarketAcquisitionBatch, ...]
    policy: MarketCompilationPolicy
    snapshot: MarketSnapshot
    dispositions: tuple[MarketObservationDisposition, ...]

    def __post_init__(self) -> None:
        batches = _normalize_batches(
            self.batches, captured_at=self.snapshot.captured_at
        )
        dispositions = tuple(self.dispositions)
        expected_snapshot, expected_dispositions = _compile_market_data(
            self.catalog,
            batches,
            captured_at=self.snapshot.captured_at,
            policy=self.policy,
        )
        if self.snapshot != expected_snapshot:
            raise ValueError(
                "market compilation snapshot does not match its catalog/acquisition basis"
            )
        if dispositions != expected_dispositions:
            raise ValueError(
                "market compilation dispositions do not match its catalog/acquisition basis"
            )
        object.__setattr__(self, "batches", batches)
        object.__setattr__(self, "dispositions", dispositions)

    @property
    def accepted_observations(self) -> tuple[MarketObservation, ...]:
        return tuple(
            disposition.observation
            for disposition in self.dispositions
            if disposition.status is MarketObservationDispositionStatus.ACCEPTED
        )


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
    resolved by arbitrary input order. The returned compilation retains the
    exact immutable inputs needed to re-derive and validate the result.
    """

    _ensure_aware(captured_at, label="market compilation captured_at")
    effective_policy = policy or MarketCompilationPolicy()
    normalized_batches = _normalize_batches(batches, captured_at=captured_at)
    snapshot, dispositions = _compile_market_data(
        catalog,
        normalized_batches,
        captured_at=captured_at,
        policy=effective_policy,
    )
    return MarketCompilation(
        catalog=catalog,
        batches=normalized_batches,
        policy=effective_policy,
        snapshot=snapshot,
        dispositions=dispositions,
    )
