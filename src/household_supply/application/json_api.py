from __future__ import annotations

from dataclasses import dataclass
from json import JSONDecodeError, dumps, loads
from typing import Any, Mapping

from household_supply.domain import Money, MultiObjectivePolicy, Quantity, SurplusPenaltyRate
from household_supply.market import MarketObservationDispositionStatus

from .models import (
    ApplicationPlanRequest,
    ApplicationPlanResult,
    ApplicationRequestError,
    InventoryInput,
    RequestedItem,
)
from .service import ApplicationMarketError, PlanApplicationService


class JsonPayloadError(ApplicationRequestError):
    """Malformed or unsupported JSON application payload."""


def _require_mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise JsonPayloadError(f"{label} must be a JSON object")
    return value


def _require_string(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise JsonPayloadError(f"{label} must be a JSON string")
    return value


def _require_keys(
    value: Mapping[str, Any],
    *,
    label: str,
    required: set[str],
    optional: set[str] | frozenset[str] = frozenset(),
) -> None:
    keys = set(value)
    missing = required - keys
    if missing:
        raise JsonPayloadError(f"{label} is missing fields: {', '.join(sorted(missing))}")
    unknown = keys - required - optional
    if unknown:
        raise JsonPayloadError(f"{label} has unknown fields: {', '.join(sorted(unknown))}")


def _parse_money(value: Any, *, label: str) -> Money:
    obj = _require_mapping(value, label=label)
    _require_keys(obj, label=label, required={"amount", "currency"})
    try:
        return Money(obj["amount"], _require_string(obj["currency"], label=f"{label}.currency"))
    except (TypeError, ValueError) as exc:
        raise JsonPayloadError(f"{label}: {exc}") from exc


def _parse_quantity(value: Any, *, label: str) -> Quantity:
    obj = _require_mapping(value, label=label)
    _require_keys(obj, label=label, required={"amount", "unit"})
    try:
        return Quantity(obj["amount"], _require_string(obj["unit"], label=f"{label}.unit"))
    except (TypeError, ValueError) as exc:
        raise JsonPayloadError(f"{label}: {exc}") from exc


def parse_plan_payload(payload: Mapping[str, Any]) -> ApplicationPlanRequest:
    payload = _require_mapping(payload, label="plan request")
    _require_keys(
        payload,
        label="plan request",
        required={"budget", "demands"},
        optional={"inventory", "objective"},
    )

    demands_raw = payload["demands"]
    if not isinstance(demands_raw, list):
        raise JsonPayloadError("plan request demands must be a JSON array")
    demands = []
    for index, raw in enumerate(demands_raw):
        obj = _require_mapping(raw, label=f"demands[{index}]")
        _require_keys(
            obj,
            label=f"demands[{index}]",
            required={"item_id", "quantity"},
        )
        demands.append(
            RequestedItem(
                _require_string(obj["item_id"], label=f"demands[{index}].item_id"),
                _parse_quantity(obj["quantity"], label=f"demands[{index}].quantity"),
            )
        )

    inventory_raw = payload.get("inventory", [])
    if not isinstance(inventory_raw, list):
        raise JsonPayloadError("plan request inventory must be a JSON array")
    inventory = []
    for index, raw in enumerate(inventory_raw):
        obj = _require_mapping(raw, label=f"inventory[{index}]")
        _require_keys(
            obj,
            label=f"inventory[{index}]",
            required={"lot_id", "item_id", "quantity"},
        )
        inventory.append(
            InventoryInput(
                lot_id=_require_string(obj["lot_id"], label=f"inventory[{index}].lot_id"),
                item_id=_require_string(obj["item_id"], label=f"inventory[{index}].item_id"),
                quantity=_parse_quantity(
                    obj["quantity"], label=f"inventory[{index}].quantity"
                ),
            )
        )

    objective = None
    if "objective" in payload:
        raw_objective = _require_mapping(payload["objective"], label="objective")
        _require_keys(
            raw_objective,
            label="objective",
            required={"additional_store_penalty"},
            optional={"surplus_penalties"},
        )
        surplus_raw = raw_objective.get("surplus_penalties", [])
        if not isinstance(surplus_raw, list):
            raise JsonPayloadError("objective surplus_penalties must be a JSON array")
        surplus = []
        for index, raw in enumerate(surplus_raw):
            obj = _require_mapping(raw, label=f"surplus_penalties[{index}]")
            _require_keys(
                obj,
                label=f"surplus_penalties[{index}]",
                required={"item_id", "cost_per_base_unit"},
            )
            try:
                surplus.append(
                    SurplusPenaltyRate(
                        item_id=_require_string(
                            obj["item_id"], label=f"surplus_penalties[{index}].item_id"
                        ),
                        cost_per_base_unit=_parse_money(
                            obj["cost_per_base_unit"],
                            label=f"surplus_penalties[{index}].cost_per_base_unit",
                        ),
                    )
                )
            except (TypeError, ValueError) as exc:
                if isinstance(exc, ApplicationRequestError):
                    raise
                raise JsonPayloadError(f"surplus_penalties[{index}]: {exc}") from exc
        try:
            objective = MultiObjectivePolicy(
                additional_store_penalty=_parse_money(
                    raw_objective["additional_store_penalty"],
                    label="objective.additional_store_penalty",
                ),
                surplus_penalties=tuple(surplus),
            )
        except (TypeError, ValueError) as exc:
            if isinstance(exc, ApplicationRequestError):
                raise
            raise JsonPayloadError(f"objective: {exc}") from exc

    return ApplicationPlanRequest(
        demands=tuple(demands),
        inventory=tuple(inventory),
        budget=_parse_money(payload["budget"], label="budget"),
        objective_policy=objective,
    )


def _money(value: Money) -> dict[str, str]:
    return {"amount": str(value.amount), "currency": value.currency}


def _quantity(value: Quantity) -> dict[str, str]:
    return {"amount": str(value.amount), "unit": value.unit}


def serialize_plan_result(result: ApplicationPlanResult) -> dict[str, Any]:
    plan = result.plan
    payload: dict[str, Any] = {
        "status": plan.status.value,
        "market": {
            "captured_at": result.market_compilation.snapshot.captured_at.isoformat(),
            "offer_count": len(result.market_compilation.snapshot.offers),
            "dispositions": {
                status.value: sum(
                    1
                    for disposition in result.market_compilation.dispositions
                    if disposition.status is status
                )
                for status in MarketObservationDispositionStatus
            },
        },
        "total_cost": _money(plan.total_cost),
        "budget_remaining": _money(plan.budget_remaining),
        "purchases": [
            {
                "offer_id": purchase.offer.id,
                "sku_id": purchase.offer.sku.id,
                "item_id": purchase.offer.sku.item.id,
                "sku_name": purchase.offer.sku.name,
                "seller_id": purchase.offer.seller_id,
                "packs": purchase.packs,
                "acquired_quantity": _quantity(purchase.acquired_quantity),
                "cost": _money(purchase.cost),
                "source_ref": (
                    purchase.offer.provenance.source_ref
                    if purchase.offer.provenance is not None
                    else ""
                ),
            }
            for purchase in plan.purchases
        ],
        "coverage": [
            {
                "item_id": coverage.item_id,
                "required": _quantity(coverage.required),
                "inventory_used": _quantity(coverage.inventory_used),
                "purchased": _quantity(coverage.purchased),
                "covered": _quantity(coverage.covered),
            }
            for coverage in plan.requirement_coverage
        ],
        "projected_leftovers": [
            {"item_id": leftover.item_id, "quantity": _quantity(leftover.quantity)}
            for leftover in plan.projected_leftovers
        ],
        "infeasibility_reasons": list(plan.infeasibility_reasons),
        "warnings": list(plan.warnings),
        "explanation": list(plan.explanation),
    }
    if plan.minimum_required_cost is not None:
        payload["minimum_required_cost"] = _money(plan.minimum_required_cost)
    if plan.objective_breakdown is not None:
        breakdown = plan.objective_breakdown
        payload["objective"] = {
            "purchase_cost": _money(breakdown.purchase_cost),
            "surplus_penalty": _money(breakdown.surplus_penalty),
            "additional_store_penalty": _money(breakdown.additional_store_penalty),
            "total_score": _money(breakdown.total_score),
            "selected_sellers": list(breakdown.selected_sellers),
            "additional_store_count": breakdown.additional_store_count,
        }
    return payload


@dataclass(frozen=True, slots=True)
class JsonApiResponse:
    status: int
    body: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PlanJsonApi:
    service: PlanApplicationService

    def handle(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
    ) -> JsonApiResponse:
        normalized_method = method.strip().upper()
        normalized_path = path.split("?", 1)[0]

        if normalized_path == "/health":
            if normalized_method != "GET":
                return JsonApiResponse(405, {"error": "method_not_allowed"})
            return JsonApiResponse(200, {"status": "ok"})

        if normalized_path != "/plans":
            return JsonApiResponse(404, {"error": "not_found"})
        if normalized_method != "POST":
            return JsonApiResponse(405, {"error": "method_not_allowed"})
        if payload is None:
            return JsonApiResponse(
                400,
                {"error": "invalid_request", "detail": "missing JSON body"},
            )

        try:
            request = parse_plan_payload(payload)
            result = self.service.plan(request)
        except ApplicationRequestError as exc:
            return JsonApiResponse(
                422, {"error": "invalid_request", "detail": str(exc)}
            )
        except ApplicationMarketError as exc:
            return JsonApiResponse(
                502, {"error": "market_unavailable", "detail": str(exc)}
            )
        return JsonApiResponse(200, serialize_plan_result(result))


def parse_json_object(text: str) -> Mapping[str, Any]:
    try:
        value = loads(text)
    except (JSONDecodeError, UnicodeDecodeError) as exc:
        raise JsonPayloadError("request body is not valid JSON") from exc
    return _require_mapping(value, label="request body")


def dump_json(value: Mapping[str, Any]) -> str:
    return dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
