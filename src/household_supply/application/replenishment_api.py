from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import urlsplit

from household_supply.domain import MultiObjectivePolicy, SurplusPenaltyRate
from household_supply.domain.money import as_decimal
from household_supply.household import (
    ConsumptionEstimationError,
    HouseholdEventRepositoryError,
    HouseholdProjectionError,
)

from .json_api import (
    JsonApiResponse,
    JsonPayloadError,
    _parse_money,
    _parse_quantity,
    _require_keys,
    _require_mapping,
    _require_string,
)
from .lifecycle_api import PlanLifecycleJsonApi, serialize_plan_record
from .models import ApplicationRequestError, RequestedItem
from .persistence import PlanRepositoryError
from .replenishment import (
    HouseholdReplenishmentRequest,
    HouseholdReplenishmentResult,
    HouseholdReplenishmentService,
)
from .service import ApplicationMarketError


def _parse_objective(value: Any) -> MultiObjectivePolicy:
    obj = _require_mapping(value, label="objective")
    _require_keys(
        obj,
        label="objective",
        required={"additional_store_penalty"},
        optional={"surplus_penalties"},
    )
    surplus_raw = obj.get("surplus_penalties", [])
    if not isinstance(surplus_raw, list):
        raise JsonPayloadError("objective surplus_penalties must be a JSON array")
    surplus = []
    for index, raw in enumerate(surplus_raw):
        entry = _require_mapping(raw, label=f"surplus_penalties[{index}]")
        _require_keys(
            entry,
            label=f"surplus_penalties[{index}]",
            required={"item_id", "cost_per_base_unit"},
        )
        try:
            surplus.append(
                SurplusPenaltyRate(
                    item_id=_require_string(
                        entry["item_id"], label=f"surplus_penalties[{index}].item_id"
                    ),
                    cost_per_base_unit=_parse_money(
                        entry["cost_per_base_unit"],
                        label=f"surplus_penalties[{index}].cost_per_base_unit",
                    ),
                )
            )
        except (TypeError, ValueError) as exc:
            raise JsonPayloadError(f"surplus_penalties[{index}]: {exc}") from exc
    try:
        return MultiObjectivePolicy(
            additional_store_penalty=_parse_money(
                obj["additional_store_penalty"],
                label="objective.additional_store_penalty",
            ),
            surplus_penalties=tuple(surplus),
        )
    except (TypeError, ValueError) as exc:
        raise JsonPayloadError(f"objective: {exc}") from exc


def parse_household_replenishment_payload(
    payload: Mapping[str, Any],
) -> HouseholdReplenishmentRequest:
    payload = _require_mapping(payload, label="household replenishment request")
    _require_keys(
        payload,
        label="household replenishment request",
        required={"budget", "horizon_days"},
        optional={"explicit_needs", "objective"},
    )

    explicit_raw = payload.get("explicit_needs", [])
    if not isinstance(explicit_raw, list):
        raise JsonPayloadError("explicit_needs must be a JSON array")
    explicit = []
    for index, raw in enumerate(explicit_raw):
        obj = _require_mapping(raw, label=f"explicit_needs[{index}]")
        _require_keys(
            obj,
            label=f"explicit_needs[{index}]",
            required={"item_id", "quantity"},
        )
        explicit.append(
            RequestedItem(
                _require_string(
                    obj["item_id"], label=f"explicit_needs[{index}].item_id"
                ),
                _parse_quantity(
                    obj["quantity"], label=f"explicit_needs[{index}].quantity"
                ),
            )
        )

    try:
        horizon_days = as_decimal(payload["horizon_days"])
    except (TypeError, ValueError) as exc:
        raise JsonPayloadError(f"horizon_days: {exc}") from exc

    objective = _parse_objective(payload["objective"]) if "objective" in payload else None
    return HouseholdReplenishmentRequest(
        budget=_parse_money(payload["budget"], label="budget"),
        horizon_days=horizon_days,
        explicit_needs=tuple(explicit),
        objective_policy=objective,
    )


def _quantity(value) -> dict[str, str]:
    return {"amount": str(value.amount), "unit": value.unit}


def serialize_household_replenishment_result(
    result: HouseholdReplenishmentResult,
) -> dict[str, Any]:
    preparation = result.preparation
    return {
        "plan": serialize_plan_record(result.plan_record),
        "household": {
            "as_of": preparation.as_of.isoformat(),
            "event_count": len(preparation.history.events),
            "state": {
                "balances": [
                    {
                        "item_id": balance.item.id,
                        "quantity": _quantity(balance.quantity),
                    }
                    for balance in preparation.state.balances
                ],
                "applied_event_ids": list(preparation.state.applied_event_ids),
            },
            "estimates": [
                {
                    "item_id": estimate.item.id,
                    "daily_quantity": _quantity(estimate.daily_quantity),
                    "sample_count": estimate.sample_count,
                    "observed_days": str(estimate.observed_days),
                    "total_consumed": _quantity(estimate.total_consumed),
                    "observed_microseconds": estimate.observed_microseconds,
                    "daily_min": _quantity(estimate.daily_min),
                    "daily_max": _quantity(estimate.daily_max),
                    "uncertainty": _quantity(estimate.uncertainty),
                }
                for estimate in preparation.estimates
            ],
            "demand": {
                "horizon_days": str(preparation.request.horizon_days),
                "demands": [
                    {
                        "item_id": demand.item.id,
                        "quantity": _quantity(demand.quantity),
                    }
                    for demand in preparation.demand_compilation.demands
                ],
                "contributions": [
                    {
                        "source_id": contribution.source_id,
                        "contribution_id": contribution.contribution_id,
                        "item_id": contribution.item.id,
                        "quantity": _quantity(contribution.quantity),
                    }
                    for contribution in preparation.demand_compilation.contributions
                ],
            },
        },
    }


class HouseholdReplenishmentJsonApi:
    """Durable household-aware /plans surface over the existing lifecycle API."""

    def __init__(self, service: HouseholdReplenishmentService) -> None:
        self.service = service
        self._plans = PlanLifecycleJsonApi(service.plans)

    def handle(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
    ) -> JsonApiResponse:
        normalized_method = method.strip().upper()
        target = urlsplit(path)
        if target.scheme or target.netloc or target.fragment:
            return JsonApiResponse(400, {"error": "invalid_request_target"})

        if target.path != "/plans" or normalized_method != "POST":
            return self._plans.handle(method, path, payload)
        if target.query:
            return JsonApiResponse(400, {"error": "invalid_query"})
        if payload is None:
            return JsonApiResponse(
                400,
                {"error": "invalid_request", "detail": "missing JSON body"},
            )

        try:
            request = parse_household_replenishment_payload(payload)
            result = self.service.create(request)
        except ApplicationRequestError as exc:
            return JsonApiResponse(
                422, {"error": "invalid_request", "detail": str(exc)}
            )
        except (HouseholdProjectionError, ConsumptionEstimationError) as exc:
            return JsonApiResponse(
                409, {"error": "household_state_conflict", "detail": str(exc)}
            )
        except HouseholdEventRepositoryError as exc:
            return JsonApiResponse(
                500, {"error": "household_storage_error", "detail": str(exc)}
            )
        except ApplicationMarketError as exc:
            return JsonApiResponse(
                502, {"error": "market_unavailable", "detail": str(exc)}
            )
        except PlanRepositoryError as exc:
            return JsonApiResponse(
                500, {"error": "storage_error", "detail": str(exc)}
            )
        return JsonApiResponse(201, serialize_household_replenishment_result(result))
