from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit

from household_supply.application import (
    JsonApiHandler,
    JsonApiResponse,
    LocalDataResetError,
    LocalDataResetService,
)
from household_supply.domain import CatalogSnapshot


def _quantity(value) -> dict[str, str]:
    return {"amount": str(value.amount), "unit": value.unit}


def serialize_web_catalog(catalog: CatalogSnapshot) -> dict[str, Any]:
    """Serialize only canonical catalog data needed by a local human UI.

    Retailer listing identities and market observations intentionally remain on the
    planning/market evidence side. The browser needs canonical items and purchasable
    package options, not authority to resolve retailer identity itself.
    """

    items_by_id = {}
    for sku in catalog.skus:
        items_by_id.setdefault(sku.item.id, sku.item)

    return {
        "items": [
            {
                "item_id": item.id,
                "name": item.canonical_name,
                "category": item.category,
                "aliases": list(item.aliases),
            }
            for item in sorted(items_by_id.values(), key=lambda item: item.id)
        ],
        "skus": [
            {
                "sku_id": sku.id,
                "item_id": sku.item.id,
                "name": sku.name,
                "brand": sku.brand,
                "package_quantity": _quantity(sku.package_quantity),
            }
            for sku in sorted(catalog.skus, key=lambda sku: sku.id)
        ],
    }


@dataclass(frozen=True, slots=True)
class HouseholdWebJsonApi:
    """Read-only browser bootstrap surface over an existing application API.

    All household mutations and planning still go through the wrapped M10 API.
    M11 adds only canonical catalog discovery required to render a client without
    duplicating catalog data inside JavaScript.
    """

    api: JsonApiHandler
    catalog: CatalogSnapshot
    reset_service: LocalDataResetService | None = None

    def accepts_json_body(self, method: str, path: str) -> bool:
        target = urlsplit(path)
        if (
            self.reset_service is not None
            and method.strip().upper() == "POST"
            and target.path == "/local-data/reset"
        ):
            return True
        policy = getattr(self.api, "accepts_json_body", None)
        return bool(callable(policy) and policy(method, path))

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

        if target.path == "/catalog":
            if target.query:
                return JsonApiResponse(400, {"error": "invalid_query"})
            if normalized_method != "GET":
                return JsonApiResponse(405, {"error": "method_not_allowed"})
            return JsonApiResponse(200, {"catalog": serialize_web_catalog(self.catalog)})

        if target.path == "/local-data/reset":
            if target.query:
                return JsonApiResponse(400, {"error": "invalid_query"})
            if self.reset_service is None:
                return JsonApiResponse(404, {"error": "not_found"})
            if normalized_method != "POST":
                return JsonApiResponse(405, {"error": "method_not_allowed"})
            if payload is None:
                return JsonApiResponse(
                    400,
                    {"error": "invalid_request", "detail": "missing JSON body"},
                )
            if set(payload) != {"confirmation"}:
                return JsonApiResponse(
                    422,
                    {
                        "error": "invalid_request",
                        "detail": "reset request must contain only confirmation",
                    },
                )
            confirmation = payload.get("confirmation")
            if not isinstance(confirmation, str) or confirmation != "RESET":
                return JsonApiResponse(
                    422,
                    {
                        "error": "invalid_request",
                        "detail": "reset confirmation must equal RESET",
                    },
                )
            try:
                result = self.reset_service.reset()
            except LocalDataResetError as exc:
                return JsonApiResponse(
                    500, {"error": "reset_failed", "detail": str(exc)}
                )
            return JsonApiResponse(
                200,
                {
                    "reset": {
                        "household_events_deleted": result.household_events_deleted,
                        "plans_deleted": result.plans_deleted,
                    }
                },
            )

        return self.api.handle(method, path, payload)
