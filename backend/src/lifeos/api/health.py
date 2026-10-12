from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from lifeos.db.session import Database
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
    checks: dict[str, str] = {}
    database: Database = request.app.state.db
    try:
        async with database.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        log.warning("readiness_failed", dependency="database", error_type=type(exc).__name__)
        checks["database"] = "error"
    try:
        await request.app.state.redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        log.warning("readiness_failed", dependency="redis", error_type=type(exc).__name__)
        checks["redis"] = "error"
    ready = all(v == "ok" for v in checks.values())
    return JSONResponse(
        {"status": "ok" if ready else "degraded", "checks": checks},
        status_code=200 if ready else 503,
    )
