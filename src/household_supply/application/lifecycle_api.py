from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit

from .json_api import JsonApiResponse, parse_plan_payload
from .models import ApplicationRequestError
from .persistence import PlanId, PlanRepositoryError, PlanRecord
from .service import ApplicationMarketError
from .lifecycle import PlanLifecycleService


def serialize_plan_record(record: PlanRecord) -> dict[str, Any]:
    return record.to_storage_payload()


def serialize_plan_record_summary(record: PlanRecord) -> dict[str, Any]:
    result = record.result.to_mapping()
    return {
        "plan_id": record.plan_id.value,
        "created_at": record.created_at.isoformat(),
        "digest": record.digest,
        "status": result.get("status"),
        "total_cost": result.get("total_cost"),
    }


def _parse_limit(query: str) -> int:
    if not query:
        return 20
    try:
        values = parse_qs(
            query,
            keep_blank_values=True,
            strict_parsing=False,
            max_num_fields=4,
        )
    except ValueError as exc:
        raise ValueError("plan history query is too large") from exc
    if set(values) != {"limit"} or len(values["limit"]) != 1:
        raise ValueError("plan history query supports only one 'limit' parameter")
    raw = values["limit"][0]
    if not raw or not raw.isascii() or not raw.isdigit():
        raise ValueError("plan history limit must be an integer from 1 to 100")
    limit = int(raw)
    if not 1 <= limit <= 100:
        raise ValueError("plan history limit must be an integer from 1 to 100")
    return limit


@dataclass(frozen=True, slots=True)
class PlanLifecycleJsonApi:
    service: PlanLifecycleService

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
        normalized_path = target.path

        if normalized_path == "/health":
            if normalized_method != "GET":
                return JsonApiResponse(405, {"error": "method_not_allowed"})
            return JsonApiResponse(200, {"status": "ok"})

        if normalized_path == "/plans":
            if normalized_method == "POST":
                if target.query:
                    return JsonApiResponse(400, {"error": "invalid_query"})
                if payload is None:
                    return JsonApiResponse(
                        400,
                        {"error": "invalid_request", "detail": "missing JSON body"},
                    )
                try:
                    request = parse_plan_payload(payload)
                    record = self.service.create(request)
                except ApplicationRequestError as exc:
                    return JsonApiResponse(
                        422, {"error": "invalid_request", "detail": str(exc)}
                    )
                except ApplicationMarketError as exc:
                    return JsonApiResponse(
                        502, {"error": "market_unavailable", "detail": str(exc)}
                    )
                except PlanRepositoryError as exc:
                    return JsonApiResponse(
                        500, {"error": "storage_error", "detail": str(exc)}
                    )
                return JsonApiResponse(201, serialize_plan_record(record))

            if normalized_method == "GET":
                try:
                    limit = _parse_limit(target.query)
                    records = self.service.list_recent(limit)
                except ValueError as exc:
                    return JsonApiResponse(
                        400, {"error": "invalid_query", "detail": str(exc)}
                    )
                except PlanRepositoryError as exc:
                    return JsonApiResponse(
                        500, {"error": "storage_error", "detail": str(exc)}
                    )
                return JsonApiResponse(
                    200,
                    {
                        "plans": [
                            serialize_plan_record_summary(record)
                            for record in records
                        ]
                    },
                )

            return JsonApiResponse(405, {"error": "method_not_allowed"})

        prefix = "/plans/"
        if normalized_path.startswith(prefix):
            if target.query:
                return JsonApiResponse(400, {"error": "invalid_query"})
            if normalized_method != "GET":
                return JsonApiResponse(405, {"error": "method_not_allowed"})
            raw_id = normalized_path[len(prefix) :]
            if not raw_id or "/" in raw_id:
                return JsonApiResponse(404, {"error": "not_found"})
            try:
                plan_id = PlanId(raw_id)
            except ValueError:
                return JsonApiResponse(404, {"error": "not_found"})
            try:
                record = self.service.get(plan_id)
            except PlanRepositoryError as exc:
                return JsonApiResponse(
                    500, {"error": "storage_error", "detail": str(exc)}
                )
            if record is None:
                return JsonApiResponse(404, {"error": "not_found"})
            return JsonApiResponse(200, serialize_plan_record(record))

        return JsonApiResponse(404, {"error": "not_found"})
