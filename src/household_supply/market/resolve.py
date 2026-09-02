from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from household_supply.domain import CatalogSnapshot, MarketObservation, SKU


class CatalogResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    CONFLICT = "conflict"


class CatalogResolutionMethod(StrEnum):
    EXPLICIT_BINDING = "explicit_binding"
    PRODUCT_IDENTIFIER = "product_identifier"
    CORROBORATED = "corroborated"


@dataclass(frozen=True, slots=True)
class CatalogResolution:
    observation_id: str
    status: CatalogResolutionStatus
    sku: SKU | None = None
    method: CatalogResolutionMethod | None = None
    candidate_sku_ids: tuple[str, ...] = ()
    detail: str = ""

    def __post_init__(self) -> None:
        observation_id = self.observation_id.strip()
        if not observation_id:
            raise ValueError("catalog resolution observation_id must not be empty")
        candidate_sku_ids = tuple(sorted(set(self.candidate_sku_ids)))
        object.__setattr__(self, "observation_id", observation_id)
        object.__setattr__(self, "candidate_sku_ids", candidate_sku_ids)
        object.__setattr__(self, "detail", self.detail.strip())
        if self.status is CatalogResolutionStatus.RESOLVED:
            if self.sku is None or self.method is None:
                raise ValueError("resolved catalog resolution requires sku and method")
            if candidate_sku_ids != (self.sku.id,):
                raise ValueError("resolved catalog resolution candidate must equal resolved SKU")
        else:
            if self.sku is not None or self.method is not None:
                raise ValueError("non-resolved catalog resolution cannot carry sku/method")


def _package_matches(observation: MarketObservation, sku: SKU) -> bool:
    observed_package = observation.package_quantity
    if observed_package is None:
        return True
    canonical = sku.package_quantity
    if not observed_package.compatible_with(canonical):
        return False
    return observed_package.as_base().base_amount == canonical.as_base().base_amount


def resolve_market_observation(
    catalog: CatalogSnapshot,
    observation: MarketObservation,
) -> CatalogResolution:
    sku_by_id = {sku.id: sku for sku in catalog.skus}
    binding_by_key = {binding.listing_key: binding for binding in catalog.bindings}
    identifier_owner = {
        identifier: sku
        for sku in catalog.skus
        for identifier in sku.identifiers
    }

    binding = binding_by_key.get(observation.listing_key)
    explicit_sku = sku_by_id[binding.sku_id] if binding is not None else None
    identifier_sku = (
        identifier_owner.get(observation.product_identifier)
        if observation.product_identifier is not None
        else None
    )

    if explicit_sku is not None and identifier_sku is not None:
        if explicit_sku.id != identifier_sku.id:
            return CatalogResolution(
                observation_id=observation.id,
                status=CatalogResolutionStatus.CONFLICT,
                candidate_sku_ids=tuple(sorted({explicit_sku.id, identifier_sku.id})),
                detail=(
                    "explicit listing binding conflicts with exact product identifier"
                ),
            )
        selected = explicit_sku
        method = CatalogResolutionMethod.CORROBORATED
    elif explicit_sku is not None:
        selected = explicit_sku
        method = CatalogResolutionMethod.EXPLICIT_BINDING
        if observation.product_identifier is not None:
            same_scheme = {
                identifier
                for identifier in selected.identifiers
                if identifier.scheme == observation.product_identifier.scheme
            }
            if same_scheme and observation.product_identifier not in same_scheme:
                return CatalogResolution(
                    observation_id=observation.id,
                    status=CatalogResolutionStatus.CONFLICT,
                    candidate_sku_ids=(selected.id,),
                    detail=(
                        "observed product identifier conflicts with bound SKU identifier"
                    ),
                )
    elif identifier_sku is not None:
        selected = identifier_sku
        method = CatalogResolutionMethod.PRODUCT_IDENTIFIER
    else:
        return CatalogResolution(
            observation_id=observation.id,
            status=CatalogResolutionStatus.UNRESOLVED,
            detail=(
                "no explicit listing binding or exact catalog product identifier match"
            ),
        )

    if not _package_matches(observation, selected):
        return CatalogResolution(
            observation_id=observation.id,
            status=CatalogResolutionStatus.CONFLICT,
            candidate_sku_ids=(selected.id,),
            detail="observed package quantity conflicts with resolved SKU",
        )

    return CatalogResolution(
        observation_id=observation.id,
        status=CatalogResolutionStatus.RESOLVED,
        sku=selected,
        method=method,
        candidate_sku_ids=(selected.id,),
        detail=(
            f"resolved by {method.value}"
            + (f" ({binding.source})" if binding is not None else "")
        ),
    )
