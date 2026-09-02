from __future__ import annotations

from dataclasses import dataclass
from json import JSONDecodeError, dumps, loads
from typing import Any, Awaitable, Callable, Mapping

from .json_api import PlanJsonApi


Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class PlanAsgiApp:
    """Tiny dependency-free ASGI adapter around PlanJsonApi.

    An embedding host may run this object with any ASGI server. The adapter owns
    transport parsing only; planning and market semantics remain in the service.
    """

    api: PlanJsonApi
    max_body_bytes: int = 1_048_576

    def __post_init__(self) -> None:
        if self.max_body_bytes <= 0:
            raise ValueError("max_body_bytes must be positive")

    async def __call__(
        self, scope: Mapping[str, Any], receive: Receive, send: Send
    ) -> None:
        if scope.get("type") != "http":
            raise RuntimeError("PlanAsgiApp supports only ASGI HTTP scopes")

        method = str(scope.get("method", ""))
        path = str(scope.get("path", ""))
        payload = None

        if method.upper() == "POST" and path == "/plans":
            headers = {
                bytes(key).lower(): bytes(value)
                for key, value in scope.get("headers", [])
            }
            content_type = (
                headers.get(b"content-type", b"")
                .split(b";", 1)[0]
                .strip()
                .lower()
            )
            if content_type != b"application/json":
                await self._send_json(
                    send,
                    415,
                    {
                        "error": "unsupported_media_type",
                        "detail": "Content-Type must be application/json",
                    },
                )
                return

            body = bytearray()
            while True:
                message = await receive()
                if message.get("type") == "http.disconnect":
                    return
                if message.get("type") != "http.request":
                    continue
                chunk = message.get("body", b"")
                if not isinstance(chunk, (bytes, bytearray)):
                    await self._send_json(send, 400, {"error": "invalid_request"})
                    return
                body.extend(chunk)
                if len(body) > self.max_body_bytes:
                    await self._send_json(send, 413, {"error": "request_too_large"})
                    return
                if not message.get("more_body", False):
                    break
            try:
                decoded = body.decode("utf-8")
                candidate = loads(decoded)
            except (UnicodeDecodeError, JSONDecodeError):
                await self._send_json(send, 400, {"error": "invalid_json"})
                return
            if not isinstance(candidate, dict):
                await self._send_json(send, 400, {"error": "invalid_json_object"})
                return
            payload = candidate

        response = self.api.handle(method, path, payload)
        await self._send_json(send, response.status, response.body)

    @staticmethod
    async def _send_json(send: Send, status: int, body: Mapping[str, Any]) -> None:
        encoded = dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(encoded)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": encoded})
