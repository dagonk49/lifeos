from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from lifeos.api.gauges import database_error, redis_error
from lifeos.logging import get_logger

router = APIRouter(tags=["health"])
log = get_logger(__name__)


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the process answers."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    """Readiness: dependencies answer. Never exposes error details."""
    errors = {
        "database": await database_error(request.app.state.db),
        "redis": await redis_error(request.app.state.redis),
    }
    for dependency, error in errors.items():
        if error:
            log.warning("readiness_failed", dependency=dependency, error_type=error)
    checks = {dependency: "error" if error else "ok" for dependency, error in errors.items()}
    ready = all(v == "ok" for v in checks.values())
    return JSONResponse(
        {"status": "ok" if ready else "degraded", "checks": checks},
        status_code=200 if ready else 503,
    )
