"""Opt-in HTTP boundary for verified weighted plans.

Never enabled by the stock Globus app. The service must be injected with an
independently trusted source of seller min/step rules; clients cannot post one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit

from household_supply.application.json_api import (
    JsonApiResponse, JsonPayloadError, _parse_money, _parse_quantity,
    _require_keys, _require_mapping, _require_string,
)
from household_supply.application.persistence import PlanId
from household_supply.application.weighted_lifecycle import (
    WeightedProcurementConflict, WeightedProcurementError,
    WeightedProcurementService, WeightedStorageError,
)
from household_supply.household import HouseholdEventId
from household_supply.market.providers.globus_online import GlobusOnlineError


def _serialize_preview(preview) -> dict[str, Any]:
    return {
        "plan": preview.record.to_mapping(),
        "expires_at": preview.expires_at.isoformat(),
        "persisted": False,
        "purchase_confirmed": False,
    }


@dataclass(frozen=True, slots=True)
class WeightedProcurementWebApi:
    service: WeightedProcurementService

    def accepts_json_body(self, method: str, path: str) -> bool:
        target = urlsplit(path)
        return (
            method.strip().upper() == "POST"
            and (
                target.path in {
                    "/weighted/plans/preview", "/weighted/plans/confirm",
                }
                or (
                    target.path.startswith("/weighted/plans/")
                    and target.path.endswith("/purchases")
                )
            )
        )

    def handle(
        self, method: str, path: str, payload: Mapping[str, Any] | None = None
    ) -> JsonApiResponse:
        target = urlsplit(path)
        if target.scheme or target.netloc or target.fragment or target.query:
            return JsonApiResponse(400, {"error": "invalid_request_target"})
        route = target.path
        verb = method.strip().upper()
        try:
            if route == "/weighted/plans/preview":
                if verb != "POST":
                    return JsonApiResponse(405, {"error": "method_not_allowed"})
                obj = _require_mapping(payload, label="weighted preview")
                _require_keys(
                    obj, label="weighted preview",
                    required={"item_id", "quantity", "budget"},
                )
                preview = self.service.preview(
                    _require_string(obj["item_id"], label="item_id"),
                    _parse_quantity(obj["quantity"], label="quantity"),
                    _parse_money(obj["budget"], label="budget"),
                )
                return JsonApiResponse(200, _serialize_preview(preview))

            if route == "/weighted/plans/confirm":
                if verb != "POST":
                    return JsonApiResponse(405, {"error": "method_not_allowed"})
                obj = _require_mapping(payload, label="weighted plan confirmation")
                _require_keys(
                    obj, label="weighted plan confirmation", required={"plan_id"}
                )
                plan = self.service.save_plan(
                    PlanId(_require_string(obj["plan_id"], label="plan_id"))
                )
                return JsonApiResponse(201, {"plan": plan.to_mapping(), "persisted": True})

            if not route.startswith("/weighted/plans/"):
                return JsonApiResponse(404, {"error": "not_found"})
            parts = route.split("/")
            if len(parts) == 4 and parts[:3] == ["", "weighted", "plans"]:
                if verb != "GET":
                    return JsonApiResponse(405, {"error": "method_not_allowed"})
                plan = self.service.repository.get_plan(PlanId(parts[3]))
                if plan is None:
                    return JsonApiResponse(404, {"error": "not_found"})
                return JsonApiResponse(200, {"plan": plan.to_mapping()})

            if len(parts) == 5 and parts[:3] == ["", "weighted", "plans"] and parts[4] == "purchases":
                if verb != "POST":
                    return JsonApiResponse(405, {"error": "method_not_allowed"})
                obj = _require_mapping(payload, label="weighted actual purchase")
                _require_keys(obj, label="weighted actual purchase", required={
                    "event_id", "actual_mass", "actual_cost", "receipt_ref",
                })
                receipt = self.service.confirm_actual(
                    plan_id=PlanId(parts[3]),
                    event_id=HouseholdEventId(
                        _require_string(obj["event_id"], label="event_id")
                    ),
                    actual_mass=_parse_quantity(obj["actual_mass"], label="actual_mass"),
                    actual_cost=_parse_money(obj["actual_cost"], label="actual_cost"),
                    receipt_ref=_require_string(obj["receipt_ref"], label="receipt_ref"),
                )
                return JsonApiResponse(201, {
                    "receipt": receipt.to_mapping(),
                    "inventory_event_id": receipt.event_id.value,
                    "purchase_confirmed": True,
                })

            return JsonApiResponse(404, {"error": "not_found"})
        except (WeightedProcurementConflict,) as exc:
            return JsonApiResponse(
                409, {"error": "weighted_conflict", "detail": str(exc)}
            )
        except (WeightedProcurementError, JsonPayloadError, TypeError, ValueError) as exc:
            return JsonApiResponse(
                422, {"error": "invalid_weighted_request", "detail": str(exc)}
            )
        except WeightedStorageError as exc:
            return JsonApiResponse(
                500, {"error": "weighted_storage_error", "detail": str(exc)}
            )
        except GlobusOnlineError as exc:
            return JsonApiResponse(
                502, {"error": "weighted_market_error", "detail": str(exc)}
            )
