from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from prometheus_client import make_asgi_app
from redis.asyncio import Redis

from lifeos import __version__
from lifeos.api.denials import install_denial_handlers
from lifeos.api.health import router as health_router
from lifeos.api.middleware import OriginCheckMiddleware, RequestContextMiddleware
from lifeos.config import Settings, get_settings
from lifeos.core.errors import install_error_handlers
from lifeos.db.session import Database
from lifeos.logging import configure_logging
from lifeos.modules.agents.router import router as agents_router
from lifeos.modules.chat.router import router as chat_router
from lifeos.modules.connectors import build_connectors
from lifeos.modules.connectors.router import router as connectors_router
from lifeos.modules.dashboard.router import router as dashboard_router
from lifeos.modules.documents.router import router as documents_router
from lifeos.modules.finance.router import router as finance_router
from lifeos.modules.goals.router import router as goals_router
from lifeos.modules.guardian.router import approvals_router
from lifeos.modules.guardian.router import router as guardian_router
from lifeos.modules.identity.router import router as auth_router
from lifeos.modules.journal.router import router as journal_router
from lifeos.modules.llm import build_provider
from lifeos.modules.memory.router import router as memory_router
from lifeos.modules.notifications.router import router as notifications_router
from lifeos.modules.privacy.router import router as privacy_router
from lifeos.modules.retrieval.embedders import build_embedder
from lifeos.modules.reviews.router import router as reviews_router
from lifeos.modules.search.router import router as search_router
from lifeos.modules.settings.router import router as settings_router
from lifeos.modules.tasks.router import router as tasks_router
from lifeos.modules.voice.router import router as voice_router


def build_v1_router() -> APIRouter:
    v1 = APIRouter(prefix="/v1")
    for router in (
        auth_router,
        settings_router,
        dashboard_router,
        tasks_router,
        goals_router,
        reviews_router,
        journal_router,
        memory_router,
        chat_router,
        agents_router,
        documents_router,
        voice_router,
        finance_router,
        search_router,
        approvals_router,
        guardian_router,
        notifications_router,
        privacy_router,
        connectors_router,
    ):
        v1.include_router(router)
    return v1


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.db = Database(settings.database_url, pool_size=settings.database_pool_size)
        app.state.redis = Redis.from_url(settings.redis_url)
        if not hasattr(app.state, "llm"):
            app.state.llm = build_provider(settings)
        if not hasattr(app.state, "embedder"):
            app.state.embedder = build_embedder(settings)
        if not hasattr(app.state, "connectors"):
            app.state.connectors = build_connectors(settings)
        try:
            yield
        finally:
            await app.state.db.dispose()
            await app.state.redis.aclose()

    app = FastAPI(
        title="LifeOS API",
        version=__version__,
        lifespan=lifespan,
        openapi_url="/v1/openapi.json" if settings.expose_openapi else None,
        docs_url="/v1/docs" if settings.expose_openapi else None,
        redoc_url=None,
    )
    install_error_handlers(app)
    install_denial_handlers(app)
    app.add_middleware(OriginCheckMiddleware, allowed_origins=[settings.public_origin])
    # Added last = outermost: every response gets a request id and is measured.
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health_router)
    app.include_router(build_v1_router())
    # Scraped from the internal network only; not routed by Traefik.
    app.mount("/metrics", make_asgi_app())
    return app
