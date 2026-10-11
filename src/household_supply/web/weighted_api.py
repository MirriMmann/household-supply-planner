"""Read-only weighted price estimates, never a persisted procurement plan."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit

from household_supply.application.json_api import (
    JsonApiResponse, JsonPayloadError, _parse_quantity, _require_keys,
    _require_mapping, _require_string,
)
from household_supply.market.providers.globus_online import GlobusOnlineError
from household_supply.market.providers.globus_weight import GlobusWeightPriceService


def _amount(value) -> dict[str, str]:
    return {"amount": str(value.amount), "unit": value.unit}


@dataclass(frozen=True, slots=True)
class WeightedReferenceWebApi:
    prices: GlobusWeightPriceService

    def accepts_json_body(self, method: str, path: str) -> bool:
        return (
            method.strip().upper() == "POST"
            and urlsplit(path).path == "/catalog/weighted-candidates/estimate"
        )

    def handle(
        self, method: str, path: str, payload: Mapping[str, Any] | None = None
    ) -> JsonApiResponse:
        target = urlsplit(path)
        if target.scheme or target.netloc or target.fragment or target.query:
            return JsonApiResponse(400, {"error": "invalid_request_target"})
        if target.path != "/catalog/weighted-candidates/estimate":
            return JsonApiResponse(404, {"error": "not_found"})
        if method.strip().upper() != "POST":
            return JsonApiResponse(405, {"error": "method_not_allowed"})
        try:
            obj = _require_mapping(payload, label="weighted reference estimate")
            _require_keys(
                obj, label="weighted reference estimate",
                required={"item_id", "quantity"},
            )
            item_id = _require_string(obj["item_id"], label="item_id")
            requested = _parse_quantity(obj["quantity"], label="quantity")
            data = self.prices.estimate(item_id=item_id, requested=requested)
            evidence = data.observation
            return JsonApiResponse(200, {
                "item_id": evidence.item_id,
                "requested": _amount(data.requested),
                "reference_unit_price": None if evidence.price is None else {
                    "amount": str(evidence.price.amount),
                    "currency": evidence.price.currency,
                    "per": _amount(evidence.reference_mass),
                },
                "estimated_upper_bound": (
                    None if data.estimated_upper_bound is None else {
                        "amount": str(data.estimated_upper_bound.amount),
                        "currency": data.estimated_upper_bound.currency,
                    }
                ),
                "listing_available": evidence.available,
                "observed_at": evidence.observed_at.isoformat(),
                "source_ref": evidence.source_ref,
                "estimate_only": True,
                "plannable": False,
                "minimum_sellable_quantity": None,
                "weight_increment": None,
                "notice": (
                    "Расчёт по справочной цене за килограмм. "
                    "Минимальный и кратный вес продажи не подтверждены; "
                    "это не план закупки, не точная сумма на кассе и не факт покупки."
                ),
            })
        except (JsonPayloadError, ValueError, TypeError) as exc:
            return JsonApiResponse(422, {"error": "invalid_request", "detail": str(exc)})
        except GlobusOnlineError as exc:
            return JsonApiResponse(
                502, {"error": "weighted_market_acquisition_failed", "detail": str(exc)}
            )
