"""Single read-only shopping needs preview, including original Kyrgyz dishes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit

from household_supply.application.json_api import (
    JsonApiResponse, JsonPayloadError, _parse_quantity, _require_keys,
    _require_mapping, _require_string,
)
from household_supply.application.shopping_readiness import (
    ShoppingDemand, ShoppingReadinessError, ShoppingReadinessService,
    recipe_candidates,
)


@dataclass(frozen=True, slots=True)
class ShoppingReadinessWebApi:
    service: ShoppingReadinessService

    def accepts_json_body(self, method: str, path: str) -> bool:
        return (
            method.strip().upper() == "POST"
            and urlsplit(path).path == "/shopping/preview"
        )

    def handle(
        self, method: str, path: str, payload: Mapping[str, Any] | None = None
    ) -> JsonApiResponse:
        url = urlsplit(path)
        if url.scheme or url.netloc or url.fragment or url.query:
            return JsonApiResponse(400, {"error": "invalid_request_target"})
        method = method.strip().upper()
        if url.path == "/shopping/recipe-candidates":
            if method != "GET":
                return JsonApiResponse(405, {"error": "method_not_allowed"})
            return JsonApiResponse(
                200, {
                    "recipes": list(recipe_candidates()),
                    "notice": (
                        "Это определения блюд, включая пока недоступные для "
                        "закупки ингредиенты. Карточки не подтверждают наличие "
                        "или возможность заказа в Globus."
                    ),
                },
            )
        if url.path != "/shopping/preview":
            return JsonApiResponse(404, {"error": "not_found"})
        if method != "POST":
            return JsonApiResponse(405, {"error": "method_not_allowed"})
        try:
            obj = _require_mapping(payload, label="shopping readiness preview")
            if set(obj) == {"recipe_id"}:
                return JsonApiResponse(200, self.service.preview(
                    recipe_id=_require_string(obj["recipe_id"], label="recipe_id")
                ))
            _require_keys(obj, label="shopping readiness preview", required={"demands"})
            raw = obj["demands"]
            if not isinstance(raw, list) or not 1 <= len(raw) <= 32:
                raise JsonPayloadError("shopping demands must be a JSON array with 1-32 lines")
            demands = []
            for index, value in enumerate(raw):
                entry = _require_mapping(value, label=f"demands[{index}]")
                _require_keys(
                    entry, label=f"demands[{index}]",
                    required={"item_id", "quantity"},
                )
                demands.append(ShoppingDemand(
                    _require_string(entry["item_id"], label=f"demands[{index}].item_id"),
                    _parse_quantity(entry["quantity"], label=f"demands[{index}].quantity"),
                ))
            return JsonApiResponse(200, self.service.preview(demands=tuple(demands)))
        except (JsonPayloadError, ShoppingReadinessError, ValueError, TypeError) as exc:
            return JsonApiResponse(
                422, {"error": "invalid_shopping_readiness", "detail": str(exc)}
            )
