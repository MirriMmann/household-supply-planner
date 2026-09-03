from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping
from urllib.parse import urlsplit

from household_supply.household import (
    DepletionEstimationError,
    DepletionLearningReport,
    HouseholdEventId,
    HouseholdEventRepositoryError,
    HouseholdProjectionError,
    HouseholdState,
    serialize_household_event,
)

from .household_operations import (
    HouseholdOperationConflictError,
    HouseholdOperationError,
    HouseholdOperationsService,
    HouseholdPlanNotFoundError,
    PurchaseConfirmationCommand,
    PurchaseConfirmationResult,
    StocktakeCommand,
)
from .json_api import (
    JsonApiResponse,
    JsonPayloadError,
    _parse_quantity,
    _require_keys,
    _require_mapping,
    _require_string,
)
from .models import ApplicationRequestError
from .persistence import PlanId, PlanRepositoryError
from .replenishment import HouseholdReplenishmentService
from .replenishment_api import HouseholdReplenishmentJsonApi


def _parse_datetime(value: Any, *, label: str) -> datetime:
    raw = _require_string(value, label=label)
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise JsonPayloadError(f"{label} must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise JsonPayloadError(f"{label} must be timezone-aware")
    return parsed


def parse_stocktake_payload(payload: Mapping[str, Any]) -> StocktakeCommand:
    obj = _require_mapping(payload, label="stocktake request")
    _require_keys(
        obj,
        label="stocktake request",
        required={"event_id", "item_id", "quantity"},
        optional={"occurred_at", "reason"},
    )
    try:
        event_id = HouseholdEventId(_require_string(obj["event_id"], label="event_id"))
    except ValueError as exc:
        raise JsonPayloadError(f"event_id: {exc}") from exc
    occurred_at = (
        _parse_datetime(obj["occurred_at"], label="occurred_at")
        if "occurred_at" in obj
        else None
    )
    reason = (
        _require_string(obj["reason"], label="reason")
        if "reason" in obj
        else "stocktake"
    )
    try:
        return StocktakeCommand(
            event_id=event_id,
            item_id=_require_string(obj["item_id"], label="item_id"),
            quantity=_parse_quantity(obj["quantity"], label="quantity"),
            occurred_at=occurred_at,
            reason=reason,
        )
    except (TypeError, ValueError) as exc:
        if isinstance(exc, ApplicationRequestError):
            raise
        raise JsonPayloadError(f"stocktake request: {exc}") from exc


def parse_purchase_confirmation_payload(
    payload: Mapping[str, Any],
) -> PurchaseConfirmationCommand:
    obj = _require_mapping(payload, label="purchase confirmation request")
    _require_keys(
        obj,
        label="purchase confirmation request",
        required={"event_id", "sku_id", "packs"},
        optional={"occurred_at"},
    )
    packs = obj["packs"]
    if type(packs) is not int:
        raise JsonPayloadError("packs must be a JSON integer")
    try:
        event_id = HouseholdEventId(_require_string(obj["event_id"], label="event_id"))
        return PurchaseConfirmationCommand(
            event_id=event_id,
            sku_id=_require_string(obj["sku_id"], label="sku_id"),
            packs=packs,
            occurred_at=(
                _parse_datetime(obj["occurred_at"], label="occurred_at")
                if "occurred_at" in obj
                else None
            ),
        )
    except (TypeError, ValueError) as exc:
        if isinstance(exc, ApplicationRequestError):
            raise
        raise JsonPayloadError(f"purchase confirmation request: {exc}") from exc


def _quantity(value) -> dict[str, str]:
    return {"amount": str(value.amount), "unit": value.unit}


def serialize_household_state(state: HouseholdState) -> dict[str, Any]:
    return {
        "as_of": state.as_of.isoformat(),
        "balances": [
            {"item_id": balance.item.id, "quantity": _quantity(balance.quantity)}
            for balance in state.balances
        ],
        "applied_event_ids": list(state.applied_event_ids),
    }


def _serialize_estimate(estimate) -> dict[str, Any] | None:
    if estimate is None:
        return None
    return {
        "item_id": estimate.item.id,
        "daily_quantity": _quantity(estimate.daily_quantity),
        "sample_count": estimate.sample_count,
        "observed_days": str(estimate.observed_days),
        "total_depleted": _quantity(estimate.total_depleted),
        "observed_microseconds": estimate.observed_microseconds,
        "daily_min": _quantity(estimate.daily_min),
        "daily_max": _quantity(estimate.daily_max),
        "uncertainty": _quantity(estimate.uncertainty),
    }


def serialize_depletion_report(report: DepletionLearningReport) -> dict[str, Any]:
    return {
        "item_id": report.item.id,
        "estimate": _serialize_estimate(report.estimate),
        "direct_observation_ids_used": list(report.direct_observation_ids_used),
        "direct_observation_ids_shadowed": list(
            report.direct_observation_ids_shadowed
        ),
        "windows": [
            {
                "period_start": window.period_start.isoformat(),
                "period_end": window.period_end.isoformat(),
                "start_stocktake_id": window.start_stocktake_id,
                "end_stocktake_id": window.end_stocktake_id,
                "start_quantity": _quantity(window.start_quantity),
                "confirmed_purchases": _quantity(window.confirmed_purchases),
                "end_quantity": _quantity(window.end_quantity),
                "explicit_consumption": _quantity(window.explicit_consumption),
                "purchase_event_ids": list(window.purchase_event_ids),
                "explicit_observation_ids": list(window.explicit_observation_ids),
                "status": window.status.value,
                "inferred_depletion": (
                    None
                    if window.inferred_depletion is None
                    else _quantity(window.inferred_depletion)
                ),
                "accepted_for_learning": window.accepted_for_learning,
            }
            for window in report.windows
        ],
    }


def serialize_purchase_confirmation(
    result: PurchaseConfirmationResult,
) -> dict[str, Any]:
    return {
        "event": serialize_household_event(result.event),
        "actual_packs": result.actual_packs,
        "plan_id": None if result.plan_id is None else result.plan_id.value,
        "planned_packs": result.planned_packs,
    }


class HouseholdClosedLoopJsonApi:
    """M10 household operations plus the existing M9 replenishment surface."""

    def __init__(
        self,
        replenishment: HouseholdReplenishmentService,
        operations: HouseholdOperationsService | None = None,
    ) -> None:
        self.replenishment = replenishment
        self.operations = operations or HouseholdOperationsService(
            household=replenishment.household,
            catalog=replenishment.plans.planner.catalog,
            plans=replenishment.plans.repository,
            clock=replenishment.clock,
        )
        self._plans = HouseholdReplenishmentJsonApi(replenishment)

    def _error_response(self, exc: Exception) -> JsonApiResponse:
        if isinstance(exc, HouseholdPlanNotFoundError):
            return JsonApiResponse(404, {"error": "not_found", "detail": str(exc)})
        if isinstance(exc, HouseholdOperationConflictError):
            return JsonApiResponse(
                409, {"error": "household_operation_conflict", "detail": str(exc)}
            )
        if isinstance(exc, (HouseholdProjectionError, DepletionEstimationError)):
            return JsonApiResponse(
                409, {"error": "household_state_conflict", "detail": str(exc)}
            )
        if isinstance(exc, ApplicationRequestError):
            return JsonApiResponse(
                422, {"error": "invalid_request", "detail": str(exc)}
            )
        if isinstance(exc, HouseholdEventRepositoryError):
            return JsonApiResponse(
                500, {"error": "household_storage_error", "detail": str(exc)}
            )
        if isinstance(exc, PlanRepositoryError):
            return JsonApiResponse(
                500, {"error": "storage_error", "detail": str(exc)}
            )
        raise exc

    def accepts_json_body(self, method: str, path: str) -> bool:
        if method.strip().upper() != "POST":
            return False
        target = urlsplit(path)
        if target.path in {"/plans", "/household/stocktakes", "/household/purchases"}:
            return True
        return target.path.startswith("/plans/") and target.path.endswith("/purchases")

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
        if target.query:
            if target.path.startswith("/household/") or target.path.endswith("/purchases"):
                return JsonApiResponse(400, {"error": "invalid_query"})
            return self._plans.handle(method, path, payload)

        try:
            if target.path == "/household/state":
                if normalized_method != "GET":
                    return JsonApiResponse(405, {"error": "method_not_allowed"})
                return JsonApiResponse(
                    200,
                    {"household": serialize_household_state(self.operations.state())},
                )

            if target.path == "/household/history":
                if normalized_method != "GET":
                    return JsonApiResponse(405, {"error": "method_not_allowed"})
                history = self.operations.history()
                return JsonApiResponse(
                    200,
                    {
                        "event_count": len(history.events),
                        "events": [
                            serialize_household_event(event) for event in history.events
                        ],
                    },
                )

            if target.path == "/household/estimates":
                if normalized_method != "GET":
                    return JsonApiResponse(405, {"error": "method_not_allowed"})
                reports = self.operations.depletion_reports()
                return JsonApiResponse(
                    200,
                    {"reports": [serialize_depletion_report(report) for report in reports]},
                )

            if target.path == "/household/stocktakes":
                if normalized_method != "POST":
                    return JsonApiResponse(405, {"error": "method_not_allowed"})
                if payload is None:
                    return JsonApiResponse(
                        400,
                        {"error": "invalid_request", "detail": "missing JSON body"},
                    )
                command = parse_stocktake_payload(payload)
                event = self.operations.record_stocktake(command)
                state = self.operations.state(as_of=event.recorded_at)
                return JsonApiResponse(
                    201,
                    {
                        "event": serialize_household_event(event),
                        "household": serialize_household_state(state),
                    },
                )

            if target.path == "/household/purchases":
                if normalized_method != "POST":
                    return JsonApiResponse(405, {"error": "method_not_allowed"})
                if payload is None:
                    return JsonApiResponse(
                        400,
                        {"error": "invalid_request", "detail": "missing JSON body"},
                    )
                command = parse_purchase_confirmation_payload(payload)
                result = self.operations.record_purchase(command)
                state = self.operations.state(as_of=result.event.recorded_at)
                return JsonApiResponse(
                    201,
                    {
                        "purchase": serialize_purchase_confirmation(result),
                        "household": serialize_household_state(state),
                    },
                )

            prefix = "/plans/"
            suffix = "/purchases"
            if target.path.startswith(prefix) and target.path.endswith(suffix):
                raw_id = target.path[len(prefix) : -len(suffix)]
                if not raw_id or "/" in raw_id:
                    return JsonApiResponse(404, {"error": "not_found"})
                if normalized_method != "POST":
                    return JsonApiResponse(405, {"error": "method_not_allowed"})
                if payload is None:
                    return JsonApiResponse(
                        400,
                        {"error": "invalid_request", "detail": "missing JSON body"},
                    )
                try:
                    plan_id = PlanId(raw_id)
                except ValueError:
                    return JsonApiResponse(404, {"error": "not_found"})
                command = parse_purchase_confirmation_payload(payload)
                result = self.operations.record_purchase(command, plan_id=plan_id)
                state = self.operations.state(as_of=result.event.recorded_at)
                return JsonApiResponse(
                    201,
                    {
                        "purchase": serialize_purchase_confirmation(result),
                        "household": serialize_household_state(state),
                    },
                )
        except Exception as exc:
            return self._error_response(exc)

        return self._plans.handle(method, path, payload)
