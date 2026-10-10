"""Minimal local-stand API for explicit routine preferences and read-only plan previews."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit

from household_supply.application import (
    ApplicationMarketError,
    ApplicationPlanner,
    JsonApiResponse,
    JsonPayloadError,
    RequestedItem,
    UsualBasket,
    UsualBasketError,
    UsualBasketItem,
    UsualBasketPreparationService,
    UsualBasketRepositoryError,
    UnknownCatalogItemError,
    catalog_items_by_id,
    serialize_plan_result,
)
from household_supply.application.json_api import (
    _parse_money,
    _parse_quantity,
    _require_keys,
    _require_mapping,
    _require_string,
)
from household_supply.domain.money import as_decimal


def _serialize_basket(basket: UsualBasket) -> dict[str, Any]:
    return {
        "items": [
            {
                "item_id": entry.item_id,
                "fallback_quantity": (
                    None
                    if entry.fallback_quantity is None
                    else {
                        "amount": str(entry.fallback_quantity.amount),
                        "unit": entry.fallback_quantity.unit,
                    }
                ),
            }
            for entry in basket.items
        ]
    }


@dataclass(frozen=True, slots=True)
class UsualBasketWebApi:
    preparation: UsualBasketPreparationService
    planner: ApplicationPlanner

    def accepts_json_body(self, method: str, path: str) -> bool:
        path_only = urlsplit(path).path
        return method.strip().upper() == "POST" and path_only in {
            "/household/usual-basket",
            "/household/usual-basket/preview",
        }

    def handle(self, method: str, path: str, payload: Mapping[str, Any] | None = None) -> JsonApiResponse:
        target = urlsplit(path)
        if target.scheme or target.netloc or target.fragment or target.query:
            return JsonApiResponse(400, {"error": "invalid_request_target"})
        method = method.strip().upper()
        try:
            if target.path == "/household/usual-basket":
                if method == "GET":
                    return JsonApiResponse(
                        200, {"usual_basket": _serialize_basket(self.preparation.basket_repository.load())}
                    )
                if method != "POST":
                    return JsonApiResponse(405, {"error": "method_not_allowed"})
                obj = _require_mapping(payload, label="usual basket")
                _require_keys(obj, label="usual basket", required={"items"})
                raw_items = obj["items"]
                if not isinstance(raw_items, list):
                    raise JsonPayloadError("usual basket items must be an array")
                entries = []
                for index, raw in enumerate(raw_items):
                    item = _require_mapping(raw, label=f"items[{index}]")
                    _require_keys(item, label=f"items[{index}]", required={"item_id"}, optional={"fallback_quantity"})
                    item_id = _require_string(item["item_id"], label=f"items[{index}].item_id")
                    fallback = item.get("fallback_quantity")
                    entries.append(
                        UsualBasketItem(
                            item_id,
                            None if fallback is None else _parse_quantity(
                                fallback, label=f"items[{index}].fallback_quantity"
                            ),
                        )
                    )
                basket = UsualBasket(tuple(entries))
                catalog_items = catalog_items_by_id(self.preparation.catalog)
                for entry in basket.items:
                    if entry.item_id not in catalog_items:
                        raise UnknownCatalogItemError(f"unknown routine item: {entry.item_id}")
                    if entry.fallback_quantity is not None and not any(
                        sku.item.id == entry.item_id
                        and entry.fallback_quantity.compatible_with(sku.package_quantity)
                        for sku in self.preparation.catalog.skus
                    ):
                        raise UsualBasketError(f"incompatible fallback quantity: {entry.item_id}")
                self.preparation.basket_repository.save(basket)
                return JsonApiResponse(200, {"usual_basket": _serialize_basket(basket)})
            if target.path == "/household/usual-basket/preview":
                if method != "POST":
                    return JsonApiResponse(405, {"error": "method_not_allowed"})
                obj = _require_mapping(payload, label="routine preview")
                _require_keys(
                    obj, label="routine preview",
                    required={"budget", "horizon_days"},
                    optional={"overrides", "exclusions"},
                )
                try:
                    horizon = as_decimal(obj["horizon_days"])
                except (TypeError, ValueError) as exc:
                    raise JsonPayloadError("invalid horizon_days") from exc
                raw_overrides = obj.get("overrides", [])
                raw_exclusions = obj.get("exclusions", [])
                if not isinstance(raw_overrides, list) or not isinstance(raw_exclusions, list):
                    raise JsonPayloadError("overrides and exclusions must be arrays")
                overrides = []
                for index, raw in enumerate(raw_overrides):
                    entry = _require_mapping(raw, label=f"overrides[{index}]")
                    _require_keys(entry, label=f"overrides[{index}]", required={"item_id", "quantity"})
                    overrides.append(RequestedItem(
                        _require_string(entry["item_id"], label=f"overrides[{index}].item_id"),
                        _parse_quantity(entry["quantity"], label=f"overrides[{index}].quantity"),
                    ))
                exclusions = tuple(
                    _require_string(item_id, label=f"exclusions[{index}]")
                    for index, item_id in enumerate(raw_exclusions)
                )
                proposal = self.preparation.prepare(
                    budget=_parse_money(obj["budget"], label="budget"),
                    horizon_days=horizon,
                    overrides=tuple(overrides),
                    exclusions=exclusions,
                )
                response = {
                    "ready": proposal.ready,
                    "needs_clarification": list(proposal.needs_clarification),
                    "choices": [
                        {"item_id": item.item_id, "basis": item.basis}
                        for item in proposal.choices
                    ],
                    "plan": None,
                    "preview_only": True,
                }
                if proposal.ready:
                    # Existing deterministic planner, never creates a PlanRecord or PurchaseEvent.
                    response["plan"] = serialize_plan_result(
                        self.planner.plan(proposal.application_request)
                    )
                return JsonApiResponse(200, response)
            return JsonApiResponse(404, {"error": "not_found"})
        except (JsonPayloadError, UsualBasketError, UnknownCatalogItemError, ValueError, TypeError) as exc:
            return JsonApiResponse(422, {"error": "invalid_request", "detail": str(exc)})
        except UsualBasketRepositoryError as exc:
            return JsonApiResponse(500, {"error": "routine_storage_failed", "detail": str(exc)})
        except ApplicationMarketError as exc:
            return JsonApiResponse(502, {"error": "market_acquisition_failed", "detail": str(exc)})
