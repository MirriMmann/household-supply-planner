from __future__ import annotations

import asyncio
from datetime import timedelta
import json

from household_supply.application import (
    InMemoryPlanRepository,
    PlanAsgiApp,
    PlanId,
    PlanLifecycleJsonApi,
    PlanLifecycleService,
)

from test_application_json_api import request_payload
from test_application_service import NOW, make_service


def make_lifecycle_api(ids=("plan-1", "plan-2", "plan-3")):
    repository = InMemoryPlanRepository()
    iterator = iter(ids)
    tick = {"value": 0}

    def clock():
        tick["value"] += 1
        return NOW + timedelta(seconds=tick["value"])

    lifecycle = PlanLifecycleService(
        make_service(),
        repository,
        clock=clock,
        id_factory=lambda: PlanId(next(iterator)),
    )
    return PlanLifecycleJsonApi(lifecycle), repository


def test_lifecycle_api_creates_and_returns_durable_record() -> None:
    api, repository = make_lifecycle_api()
    response = api.handle("POST", "/plans", request_payload())
    assert response.status == 201
    assert response.body["plan_id"] == "plan-1"
    assert response.body["result"]["status"] == "feasible"
    assert response.body["result"]["total_cost"]["amount"] == "310"
    assert response.body["market_evidence"]["batches"][0]["provider_id"] == "fixture"
    assert repository.get(PlanId("plan-1")) is not None


def test_lifecycle_api_get_returns_saved_record_without_replanning() -> None:
    api, _ = make_lifecycle_api()
    created = api.handle("POST", "/plans", request_payload())
    fetched = api.handle("GET", "/plans/plan-1")
    assert fetched.status == 200
    assert fetched.body == created.body


def test_lifecycle_api_lists_recent_records_with_limit() -> None:
    api, _ = make_lifecycle_api()
    api.handle("POST", "/plans", request_payload())
    api.handle("POST", "/plans", request_payload())
    response = api.handle("GET", "/plans?limit=1")
    assert response.status == 200
    assert [row["plan_id"] for row in response.body["plans"]] == ["plan-2"]
    assert "market_evidence" not in response.body["plans"][0]


def test_lifecycle_api_query_is_strict() -> None:
    api, _ = make_lifecycle_api()
    for target in (
        "/plans?limit=0",
        "/plans?limit=101",
        "/plans?limit=x",
        "/plans?offset=1",
        "/plans?limit=1&limit=2",
    ):
        response = api.handle("GET", target)
        assert response.status == 400
        assert response.body["error"] == "invalid_query"


def test_lifecycle_api_missing_or_invalid_plan_is_404() -> None:
    api, _ = make_lifecycle_api()
    assert api.handle("GET", "/plans/missing").status == 404
    assert api.handle("GET", "/plans/../bad").status == 404
    assert api.handle("GET", "/plans/A").status == 404


def test_lifecycle_api_preserves_m6_request_and_market_error_semantics() -> None:
    api, _ = make_lifecycle_api()
    payload = request_payload()
    payload["demands"][0]["item_id"] = "unknown"
    response = api.handle("POST", "/plans", payload)
    assert response.status == 422
    assert response.body["error"] == "invalid_request"

    service = make_service()

    class FailingProvider:
        provider_id = "fixture"

        def acquire(self):
            raise RuntimeError("offline")

    failing = type(service)(service.catalog, (FailingProvider(),), clock=lambda: NOW)
    lifecycle = PlanLifecycleService(
        failing,
        InMemoryPlanRepository(),
        clock=lambda: NOW + timedelta(seconds=1),
        id_factory=lambda: PlanId("failed"),
    )
    response = PlanLifecycleJsonApi(lifecycle).handle("POST", "/plans", request_payload())
    assert response.status == 502
    assert response.body["error"] == "market_unavailable"


def test_lifecycle_api_maps_repository_write_failure_to_500() -> None:
    class FailingRepository:
        def save(self, record):
            raise RuntimeError("disk")

        def get(self, plan_id):
            return None

        def list_recent(self, limit):
            return ()

    # Only declared repository failures are normalized. Programming/runtime
    # errors must not be mislabeled as a storage condition.
    lifecycle = PlanLifecycleService(
        make_service(),
        FailingRepository(),
        clock=lambda: NOW + timedelta(seconds=1),
        id_factory=lambda: PlanId("failed"),
    )
    try:
        PlanLifecycleJsonApi(lifecycle).handle("POST", "/plans", request_payload())
    except RuntimeError as exc:
        assert str(exc) == "disk"
    else:  # pragma: no cover
        raise AssertionError("unexpected runtime error was masked")


def test_lifecycle_api_maps_declared_repository_failure_to_500() -> None:
    from household_supply.application import PlanRepositoryError

    class FailingRepository:
        def save(self, record):
            raise PlanRepositoryError("disk unavailable")

        def get(self, plan_id):
            return None

        def list_recent(self, limit):
            return ()

    lifecycle = PlanLifecycleService(
        make_service(),
        FailingRepository(),
        clock=lambda: NOW + timedelta(seconds=1),
        id_factory=lambda: PlanId("failed"),
    )
    response = PlanLifecycleJsonApi(lifecycle).handle("POST", "/plans", request_payload())
    assert response.status == 500
    assert response.body["error"] == "storage_error"


async def invoke(app, *, method: str, path: str, query: bytes = b"", body: bytes = b""):
    sent = []
    messages = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    headers = []
    if method.upper() == "POST":
        headers.append((b"content-type", b"application/json"))
    await app(
        {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": query,
            "headers": headers,
        },
        receive,
        send,
    )
    return sent[0]["status"], json.loads(sent[1]["body"].decode("utf-8"))


def test_asgi_passes_query_string_to_lifecycle_api() -> None:
    api, _ = make_lifecycle_api()
    app = PlanAsgiApp(api)
    payload = json.dumps(request_payload()).encode("utf-8")
    assert asyncio.run(invoke(app, method="POST", path="/plans", body=payload))[0] == 201
    assert asyncio.run(invoke(app, method="POST", path="/plans", body=payload))[0] == 201
    status, body = asyncio.run(
        invoke(app, method="GET", path="/plans", query=b"limit=1")
    )
    assert status == 200
    assert [row["plan_id"] for row in body["plans"]] == ["plan-2"]


def test_asgi_get_plan_record_never_requires_request_body() -> None:
    api, _ = make_lifecycle_api()
    app = PlanAsgiApp(api)
    payload = json.dumps(request_payload()).encode("utf-8")
    asyncio.run(invoke(app, method="POST", path="/plans", body=payload))
    status, body = asyncio.run(invoke(app, method="GET", path="/plans/plan-1"))
    assert status == 200
    assert body["plan_id"] == "plan-1"


def test_lifecycle_api_rejects_query_on_create_and_record_get() -> None:
    api, _ = make_lifecycle_api()
    assert api.handle("POST", "/plans?x=1", request_payload()).status == 400
    api.handle("POST", "/plans", request_payload())
    assert api.handle("GET", "/plans/plan-1?x=1").status == 400


def test_asgi_bounds_query_string() -> None:
    api, _ = make_lifecycle_api()
    app = PlanAsgiApp(api, max_query_bytes=4)
    status, body = asyncio.run(
        invoke(app, method="GET", path="/plans", query=b"limit=1")
    )
    assert status == 414
    assert body["error"] == "request_target_too_large"
