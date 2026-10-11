"""Opt-in manual receipt reconciliation; not a retailer checkout integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping
from urllib.parse import urlsplit

from household_supply.application.json_api import (
    JsonApiResponse, JsonPayloadError, _parse_money, _parse_quantity,
    _require_keys, _require_mapping, _require_string,
)
from household_supply.application.mixed_plan import (
    MixedPlanConflict, MixedPlanStorageError,
)
from household_supply.application.mixed_receipt import (
    MixedActualLine, MixedReceiptConflict, MixedReceiptError,
    MixedReceiptService, MixedReceiptStorageError,
)
from household_supply.application.persistence import PlanId
from household_supply.household import HouseholdEventRepositoryError


@dataclass(frozen=True, slots=True)
class MixedReceiptWebApi:
    service: MixedReceiptService

    @staticmethod
    def _id(path: str) -> str | None:
        parts = path.split("/")
        if (
            len(parts) == 5
            and parts[:3] == ["", "mixed", "plans"]
            and parts[4] == "receipt"
        ):
            return parts[3]
        return None

    def accepts_json_body(self, method: str, path: str) -> bool:
        return (
            method.strip().upper() == "POST"
            and self._id(urlsplit(path).path) is not None
        )

    def handle(
        self, method: str, path: str, payload: Mapping[str, Any] | None = None,
    ) -> JsonApiResponse:
        target = urlsplit(path)
        if target.scheme or target.netloc or target.fragment or target.query:
            return JsonApiResponse(400, {"error": "invalid_request_target"})
        raw_plan_id = self._id(target.path)
        if raw_plan_id is None:
            return JsonApiResponse(404, {"error": "not_found"})
        verb = method.strip().upper()
        if verb not in {"GET", "POST"}:
            return JsonApiResponse(405, {"error": "method_not_allowed"})
        try:
            plan_id = PlanId(raw_plan_id)
            if verb == "GET":
                return JsonApiResponse(200, self.service.status(plan_id))
            obj = _require_mapping(payload, label="mixed actual receipt")
            _require_keys(obj, label="mixed actual receipt", required={
                "receipt_ref", "occurred_at", "lines", "delivery_and_fees",
                "discount", "total_paid", "price_changes_approved",
            })
            raw_lines = obj["lines"]
            if not isinstance(raw_lines, list) or not 1 <= len(raw_lines) <= 32:
                raise JsonPayloadError("receipt lines require 1-32 entries")
            parsed = []
            for index, raw in enumerate(raw_lines):
                entry = _require_mapping(raw, label=f"receipt.lines[{index}]")
                kind = _require_string(entry.get("kind"), label="kind")
                if kind == "fixed_package":
                    _require_keys(entry, label="packaged receipt line", required={
                        "kind", "item_id", "sku_id", "packs", "paid",
                    })
                    packs = entry["packs"]
                    if type(packs) is not int or packs < 1:
                        raise JsonPayloadError("receipt packs must be positive integer")
                    sku_id = _require_string(entry["sku_id"], label="sku_id")
                    sku = next(
                        (sku for sku in self.service.catalog.skus if sku.id == sku_id), None
                    )
                    if sku is None:
                        raise JsonPayloadError("receipt SKU not present in catalog")
                    # No implicit unit arithmetic: explicit exact decimal
                    # multiplication reproduces canonical sold pack quantity.
                    from household_supply.domain import Quantity
                    from household_supply.domain._decimal import multiply_decimal_by_int_exact
                    quantity = Quantity(
                        multiply_decimal_by_int_exact(sku.package_quantity.amount, packs),
                        sku.package_quantity.unit,
                    ).as_base()
                elif kind == "weighted":
                    _require_keys(entry, label="weighted receipt line", required={
                        "kind", "item_id", "actual_mass", "paid",
                    })
                    packs = None
                    sku_id = ""
                    quantity = _parse_quantity(
                        entry["actual_mass"], label=f"receipt.lines[{index}].actual_mass",
                    )
                else:
                    raise JsonPayloadError("receipt line kind unsupported")
                parsed.append(MixedActualLine(
                    _require_string(entry["item_id"], label="item_id"),
                    kind, sku_id, packs, quantity,
                    _parse_money(entry["paid"], label="paid"),
                ))
            authorized = obj["price_changes_approved"]
            if type(authorized) is not bool:
                raise JsonPayloadError("price_changes_approved must be a JSON boolean")
            occurred = datetime.fromisoformat(
                _require_string(obj["occurred_at"], label="occurred_at")
            )
            outcome = self.service.confirm(
                plan_id=plan_id,
                receipt_ref=_require_string(obj["receipt_ref"], label="receipt_ref"),
                occurred_at=occurred,
                lines=tuple(parsed),
                delivery_and_fees=_parse_money(
                    obj["delivery_and_fees"], label="delivery_and_fees"
                ),
                discount=_parse_money(obj["discount"], label="discount"),
                total_paid=_parse_money(obj["total_paid"], label="total_paid"),
                price_changes_approved=authorized,
            )
            return JsonApiResponse(
                201 if outcome["state"] == "completed" else 202, outcome
            )
        except (MixedReceiptConflict, MixedPlanConflict) as exc:
            return JsonApiResponse(
                409, {"error": "mixed_receipt_conflict", "detail": str(exc)}
            )
        except (MixedReceiptError, JsonPayloadError, ValueError, TypeError) as exc:
            return JsonApiResponse(
                422, {"error": "invalid_mixed_receipt", "detail": str(exc)}
            )
        except (MixedReceiptStorageError, MixedPlanStorageError, HouseholdEventRepositoryError) as exc:
            return JsonApiResponse(
                500, {"error": "mixed_receipt_storage_error", "detail": str(exc)}
            )
