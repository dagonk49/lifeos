"""Pure ASGI middlewares: request context, security headers, CSRF origin check."""

import json
import re
import time
import uuid

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from lifeos.core.errors import PROBLEM_MEDIA_TYPE, problem_body
from lifeos.logging import get_logger
from lifeos.observability.metrics import HTTP_REQUESTS

log = get_logger("lifeos.http")

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"no-store"),
]


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return str(value.decode("latin-1"))
    return None


class RequestContextMiddleware:
    """Request id, structured access log (route template only) and metrics."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = _header(scope, b"x-request-id")
        request_id = incoming if incoming and _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        status_code = 500
        start = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                existing = {k.lower() for k, _ in headers}
                headers.extend(h for h in _SECURITY_HEADERS if h[0] not in existing)
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration = time.perf_counter() - start
            route = scope.get("route")
            route_path = getattr(route, "path", None) or "unmatched"
            if not route_path.startswith("/metrics"):
                HTTP_REQUESTS.labels(scope["method"], route_path, str(status_code)).observe(
                    duration
                )
                log.info(
                    "request",
                    method=scope["method"],
                    route=route_path,
                    status=status_code,
                    duration_ms=round(duration * 1000, 1),
                )
            structlog.contextvars.clear_contextvars()


class OriginCheckMiddleware:
    """CSRF defence in depth (cookies are already SameSite=Strict).

    Every state-changing request under /v1 must carry an Origin header matching the public
    origin of the web client.
    """

    def __init__(self, app: ASGIApp, allowed_origins: list[str]) -> None:
        self.app = app
        self.allowed = {o.rstrip("/") for o in allowed_origins}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope["method"] in _UNSAFE_METHODS
            and scope["path"].startswith("/v1/")
        ):
            origin = _header(scope, b"origin")
            if origin is None or origin.rstrip("/") not in self.allowed:
                body = json.dumps(
                    problem_body(403, "origin_mismatch", "Origin not allowed")
                ).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": 403,
                        "headers": [
                            (b"content-type", PROBLEM_MEDIA_TYPE.encode()),
                            (b"content-length", str(len(body)).encode()),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)
