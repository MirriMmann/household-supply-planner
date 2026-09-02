from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from household_supply.domain import CatalogBinding, ProductIdentifier, SKU
from household_supply.market import MarketCompilation

from .json_api import serialize_plan_result
from .models import ApplicationPlanRequest
from .persistence import PlanId, PlanRecord, PlanRepository
from .service import PlanApplicationService


LifecycleClock = Callable[[], datetime]
PlanIdFactory = Callable[[], PlanId]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_plan_id() -> PlanId:
    return PlanId(uuid4().hex)


def _require_aware(value: datetime, *, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _money(value) -> dict[str, str]:
    return {"amount": str(value.amount), "currency": value.currency}


def _quantity(value) -> dict[str, str]:
    return {"amount": str(value.amount), "unit": value.unit}


def serialize_plan_request(request: ApplicationPlanRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "budget": _money(request.budget),
        "demands": [
            {"item_id": demand.item_id, "quantity": _quantity(demand.quantity)}
            for demand in request.demands
        ],
        "inventory": [
            {
                "lot_id": entry.lot_id,
                "item_id": entry.item_id,
                "quantity": _quantity(entry.quantity),
            }
            for entry in request.inventory
        ],
        "objective": None,
    }
    if request.objective_policy is not None:
        payload["objective"] = {
            "additional_store_penalty": _money(
                request.objective_policy.additional_store_penalty
            ),
            "surplus_penalties": [
                {
                    "item_id": penalty.item_id,
                    "cost_per_base_unit": _money(penalty.cost_per_base_unit),
                }
                for penalty in request.objective_policy.surplus_penalties
            ],
        }
    return payload


def _identifier(value: ProductIdentifier | None) -> dict[str, str] | None:
    if value is None:
        return None
    return {"scheme": value.scheme, "value": value.value}


def _sku(value: SKU) -> dict[str, Any]:
    return {
        "id": value.id,
        "item": {
            "id": value.item.id,
            "canonical_name": value.item.canonical_name,
            "category": value.item.category,
            "aliases": list(value.item.aliases),
        },
        "name": value.name,
        "brand": value.brand,
        "package_quantity": _quantity(value.package_quantity),
        "identifiers": [_identifier(identifier) for identifier in value.identifiers],
    }


def _binding(value: CatalogBinding) -> dict[str, Any]:
    return {
        "listing_key": {
            "provider_id": value.listing_key.provider_id,
            "seller_id": value.listing_key.seller_id,
            "external_product_id": value.listing_key.external_product_id,
        },
        "sku_id": value.sku_id,
        "source": value.source,
    }


def _timedelta_microseconds(value) -> int:
    return (
        value.days * 86_400_000_000
        + value.seconds * 1_000_000
        + value.microseconds
    )


def serialize_market_evidence(compilation: MarketCompilation) -> dict[str, Any]:
    """Serialize the complete immutable M4 basis used by one application result."""

    return {
        "captured_at": compilation.snapshot.captured_at.isoformat(),
        "policy": {
            "max_observation_age_microseconds": (
                None
                if compilation.policy.max_observation_age is None
                else _timedelta_microseconds(compilation.policy.max_observation_age)
            )
        },
        "catalog": {
            "skus": [_sku(sku) for sku in compilation.catalog.skus],
            "bindings": [_binding(binding) for binding in compilation.catalog.bindings],
        },
        "batches": [
            {
                "provider_id": batch.provider_id,
                "acquired_at": batch.acquired_at.isoformat(),
                "observations": [
                    {
                        "id": observation.id,
                        "provider_id": observation.provider_id,
                        "seller_id": observation.seller_id,
                        "external_product_id": observation.external_product_id,
                        "price": (
                            None
                            if observation.price is None
                            else _money(observation.price)
                        ),
                        "observed_at": observation.observed_at.isoformat(),
                        "available": observation.available,
                        "product_identifier": _identifier(
                            observation.product_identifier
                        ),
                        "package_quantity": (
                            None
                            if observation.package_quantity is None
                            else _quantity(observation.package_quantity)
                        ),
                        "name": observation.name,
                        "brand": observation.brand,
                        "source_ref": observation.source_ref,
                    }
                    for observation in batch.observations
                ],
            }
            for batch in compilation.batches
        ],
        "dispositions": [
            {
                "observation_id": disposition.observation.id,
                "status": disposition.status.value,
                "detail": disposition.detail,
                "resolution": (
                    None
                    if disposition.resolution is None
                    else {
                        "status": disposition.resolution.status.value,
                        "sku_id": (
                            None
                            if disposition.resolution.sku is None
                            else disposition.resolution.sku.id
                        ),
                        "method": (
                            None
                            if disposition.resolution.method is None
                            else disposition.resolution.method.value
                        ),
                        "candidate_sku_ids": list(
                            disposition.resolution.candidate_sku_ids
                        ),
                        "detail": disposition.resolution.detail,
                    }
                ),
            }
            for disposition in compilation.dispositions
        ],
        "offers": [
            {
                "id": offer.id,
                "sku_id": offer.sku.id,
                "seller_id": offer.seller_id,
                "price": _money(offer.price),
                "observed_at": offer.observed_at.isoformat(),
                "source": offer.source,
                "available": offer.available,
                "provenance": (
                    None
                    if offer.provenance is None
                    else {
                        "observation_id": offer.provenance.observation_id,
                        "provider_id": offer.provenance.listing_key.provider_id,
                        "seller_id": offer.provenance.listing_key.seller_id,
                        "external_product_id": (
                            offer.provenance.listing_key.external_product_id
                        ),
                        "source_ref": offer.provenance.source_ref,
                    }
                ),
            }
            for offer in compilation.snapshot.offers
        ],
    }


def build_plan_record(
    *,
    plan_id: PlanId,
    created_at: datetime,
    result,
) -> PlanRecord:
    return PlanRecord.create(
        plan_id=plan_id,
        created_at=created_at,
        request=serialize_plan_request(result.request),
        result=serialize_plan_result(result),
        market_evidence=serialize_market_evidence(result.market_compilation),
    )


@dataclass(frozen=True, slots=True)
class PlanLifecycleService:
    planner: PlanApplicationService
    repository: PlanRepository
    clock: LifecycleClock = _utc_now
    id_factory: PlanIdFactory = _new_plan_id

    def __post_init__(self) -> None:
        if not callable(self.clock):
            raise TypeError("plan lifecycle clock must be callable")
        if not callable(self.id_factory):
            raise TypeError("plan lifecycle id_factory must be callable")

    def create(self, request: ApplicationPlanRequest) -> PlanRecord:
        plan_id = self.id_factory()
        if not isinstance(plan_id, PlanId):
            raise TypeError("plan lifecycle id_factory must return PlanId")
        if self.repository.get(plan_id) is not None:
            from .persistence import PlanRepositoryError

            raise PlanRepositoryError(f"plan record already exists: {plan_id}")

        result = self.planner.plan(request)
        created_at = self.clock()
        _require_aware(created_at, label="plan record created_at")
        if created_at < result.market_compilation.snapshot.captured_at:
            raise RuntimeError(
                "plan record creation time precedes application market capture"
            )
        record = build_plan_record(
            plan_id=plan_id,
            created_at=created_at,
            result=result,
        )
        self.repository.save(record)
        return record

    def get(self, plan_id: PlanId) -> PlanRecord | None:
        return self.repository.get(plan_id)

    def list_recent(self, limit: int = 20) -> tuple[PlanRecord, ...]:
        return self.repository.list_recent(limit)
