"""Opt-in mixed package+weight plan API. NOT enabled by ordinary Globus."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit

from household_supply.application.json_api import (
    JsonApiResponse, JsonPayloadError, _parse_money, _parse_quantity,
    _require_keys, _require_mapping, _require_string,
)
from household_supply.application.mixed_plan import (
    MixedPlanConflict, MixedPlanError, MixedPlanService, MixedPlanStorageError,
)
from household_supply.application.persistence import PlanId
from household_supply.application.shopping_readiness import ShoppingDemand
from household_supply.application.service import ApplicationMarketError
from household_supply.market.providers.globus_online import GlobusOnlineError


@dataclass(frozen=True, slots=True)
class MixedPlanWebApi:
    service: MixedPlanService

    def accepts_json_body(self, method: str, path: str) -> bool:
        return (
            method.strip().upper() == "POST"
            and urlsplit(path).path in {"/mixed/plans/preview", "/mixed/plans/confirm"}
        )

    def handle(
        self, method: str, path: str, payload: Mapping[str, Any] | None = None
    ) -> JsonApiResponse:
        target = urlsplit(path)
        if target.scheme or target.netloc or target.fragment or target.query:
            return JsonApiResponse(400, {"error": "invalid_request_target"})
        method = method.strip().upper()
        try:
            if target.path == "/mixed/plans/preview":
                if method != "POST":
                    return JsonApiResponse(405, {"error": "method_not_allowed"})
                data = _require_mapping(payload, label="mixed plan preview")
                budget = _parse_money(data.get("budget"), label="budget")
                if set(data) == {"budget", "recipe_id"}:
                    preview = self.service.preview(
                        recipe_id=_require_string(data["recipe_id"], label="recipe_id"),
                        budget=budget,
                    )
                else:
                    _require_keys(
                        data, label="mixed plan preview",
                        required={"budget", "demands"},
                    )
                    raw = data["demands"]
                    if not isinstance(raw, list) or not 1 <= len(raw) <= 32:
                        raise JsonPayloadError("demands must be an array of 1-32 lines")
                    demands = []
                    for index, value in enumerate(raw):
                        item = _require_mapping(value, label=f"demands[{index}]")
                        _require_keys(item, label=f"demands[{index}]", required={"item_id", "quantity"})
                        demands.append(ShoppingDemand(
                            _require_string(item["item_id"], label=f"demands[{index}].item_id"),
                            _parse_quantity(item["quantity"], label=f"demands[{index}].quantity"),
                        ))
                    preview = self.service.preview(demands=tuple(demands), budget=budget)
                return JsonApiResponse(200, {
                    "plan": preview.record.to_mapping(),
                    "preview_expires_at": preview.expires_at.isoformat(),
                    "persisted": False,
                    "checkout_confirmed": False,
                    "notice": (
                        "Это предварительный список товаров и сумма без доставки. "
                        "Весовая цена окончательно определяется по фактическому чеку. "
                        "Покупка не оформлена."
                    ),
                })
            if target.path == "/mixed/plans/confirm":
                if method != "POST":
                    return JsonApiResponse(405, {"error": "method_not_allowed"})
                data = _require_mapping(payload, label="mixed plan confirmation")
                _require_keys(data, label="mixed plan confirmation", required={"plan_id"})
                plan = self.service.confirm(
                    PlanId(_require_string(data["plan_id"], label="plan_id"))
                )
                return JsonApiResponse(201, {
                    "plan": plan.to_mapping(), "persisted": True,
                    "checkout_confirmed": False,
                })
            parts = target.path.split("/")
            if len(parts) == 4 and parts[:3] == ["", "mixed", "plans"]:
                if method != "GET":
                    return JsonApiResponse(405, {"error": "method_not_allowed"})
                plan = self.service.repository.get(PlanId(parts[3]))
                if plan is None:
                    return JsonApiResponse(404, {"error": "not_found"})
                return JsonApiResponse(200, {
                    "plan": plan.to_mapping(), "checkout_confirmed": False,
                })
            return JsonApiResponse(404, {"error": "not_found"})
        except MixedPlanConflict as exc:
            return JsonApiResponse(409, {"error": "mixed_plan_conflict", "detail": str(exc)})
        except (JsonPayloadError, MixedPlanError, ValueError, TypeError) as exc:
            return JsonApiResponse(422, {"error": "invalid_mixed_plan", "detail": str(exc)})
        except MixedPlanStorageError as exc:
            return JsonApiResponse(500, {"error": "mixed_plan_storage_failed", "detail": str(exc)})
        except (ApplicationMarketError, GlobusOnlineError) as exc:
            return JsonApiResponse(502, {"error": "mixed_market_unavailable", "detail": str(exc)})
