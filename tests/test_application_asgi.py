from __future__ import annotations

import asyncio
import json

from household_supply.application import PlanAsgiApp, PlanJsonApi

from test_application_json_api import request_payload
from test_application_service import make_service


async def invoke(app, *, method: str, path: str, body: bytes = b"", content_type: bytes | None = b"application/json"):
    sent = []
    messages = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    headers = []
    if content_type is not None:
        headers.append((b"content-type", content_type))
    await app(
        {"type": "http", "method": method, "path": path, "headers": headers},
        receive,
        send,
    )
    status = sent[0]["status"]
    response_body = json.loads(sent[1]["body"].decode("utf-8"))
    return status, response_body


def make_app(*, max_body_bytes: int = 1_048_576):
    return PlanAsgiApp(PlanJsonApi(make_service()), max_body_bytes=max_body_bytes)


def test_asgi_post_plans() -> None:
    status, body = asyncio.run(
        invoke(
            make_app(),
            method="POST",
            path="/plans",
            body=json.dumps(request_payload()).encode("utf-8"),
        )
    )
    assert status == 200
    assert body["status"] == "feasible"


def test_asgi_health() -> None:
    status, body = asyncio.run(invoke(make_app(), method="GET", path="/health"))
    assert status == 200
    assert body == {"status": "ok"}


def test_asgi_requires_json_content_type() -> None:
    status, body = asyncio.run(
        invoke(make_app(), method="POST", path="/plans", body=b"{}", content_type=b"text/plain")
    )
    assert status == 415
    assert body["error"] == "unsupported_media_type"


def test_asgi_rejects_invalid_json() -> None:
    status, body = asyncio.run(
        invoke(make_app(), method="POST", path="/plans", body=b"{")
    )
    assert status == 400
    assert body["error"] == "invalid_json"


def test_asgi_rejects_non_object_json() -> None:
    status, body = asyncio.run(
        invoke(make_app(), method="POST", path="/plans", body=b"[]")
    )
    assert status == 400
    assert body["error"] == "invalid_json_object"


def test_asgi_enforces_body_limit() -> None:
    status, body = asyncio.run(
        invoke(make_app(max_body_bytes=2), method="POST", path="/plans", body=b"{}x")
    )
    assert status == 413
    assert body["error"] == "request_too_large"


def test_asgi_method_and_route_statuses() -> None:
    status, _ = asyncio.run(invoke(make_app(), method="GET", path="/plans"))
    assert status == 405
    status, _ = asyncio.run(invoke(make_app(), method="GET", path="/missing"))
    assert status == 404


def test_asgi_accepts_chunked_json_body() -> None:
    app = make_app()
    encoded = json.dumps(request_payload()).encode("utf-8")
    sent = []
    messages = [
        {"type": "http.request", "body": encoded[:17], "more_body": True},
        {"type": "http.request", "body": encoded[17:], "more_body": False},
    ]

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    asyncio.run(
        app(
            {
                "type": "http",
                "method": "POST",
                "path": "/plans",
                "headers": [(b"content-type", b"application/json; charset=utf-8")],
            },
            receive,
            send,
        )
    )
    assert sent[0]["status"] == 200
    assert json.loads(sent[1]["body"])["status"] == "feasible"
