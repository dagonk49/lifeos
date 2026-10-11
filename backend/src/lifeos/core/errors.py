"""RFC 9457 problem details (see docs/conventions-api.md)."""

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from lifeos.logging import get_logger

PROBLEM_MEDIA_TYPE = "application/problem+json"

log = get_logger(__name__)


class ProblemError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        title: str,
        detail: str | None = None,
        *,
        headers: dict[str, str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}: {detail or title}")
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.headers = headers
        self.extra = extra or {}


def not_found(code: str, title: str, detail: str | None = None) -> ProblemError:
    return ProblemError(404, code, title, detail)


def conflict(code: str, title: str, detail: str | None = None) -> ProblemError:
    return ProblemError(409, code, title, detail)


def problem_body(
    status: int,
    code: str,
    title: str,
    detail: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": f"urn:lifeos:problem:{code}",
        "title": title,
        "status": status,
        "code": code,
    }
    if detail:
        body["detail"] = detail
    request_id = structlog.contextvars.get_contextvars().get("request_id")
    if request_id:
        body["request_id"] = request_id
    if extra:
        body.update(extra)
    return body


def problem_response(
    status: int,
    code: str,
    title: str,
    detail: str | None = None,
    *,
    headers: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    return JSONResponse(
        problem_body(status, code, title, detail, extra),
        status_code=status,
        media_type=PROBLEM_MEDIA_TYPE,
        headers=headers,
    )


_HTTP_CODES = {
    400: ("bad_request", "Bad request"),
    401: ("unauthenticated", "Authentication required"),
    403: ("forbidden", "Forbidden"),
    404: ("not_found", "Not found"),
    405: ("method_not_allowed", "Method not allowed"),
    429: ("rate_limited", "Too many requests"),
}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def _problem(_: Request, exc: ProblemError) -> JSONResponse:
        return problem_response(
            exc.status, exc.code, exc.title, exc.detail, headers=exc.headers, extra=exc.extra
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Never echo the submitted input back: it may contain personal data.
        errors = [
            {"loc": list(err.get("loc", ())), "msg": err.get("msg"), "type": err.get("type")}
            for err in exc.errors()
        ]
        return problem_response(
            422, "validation_error", "Validation error", extra={"errors": errors}
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code, title = _HTTP_CODES.get(exc.status_code, ("http_error", "HTTP error"))
        headers = dict(exc.headers) if exc.headers else None
        return problem_response(exc.status_code, code, title, headers=headers)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", error_type=type(exc).__name__)
        return problem_response(500, "internal_error", "Internal server error")
