"""FastAPI application factory and middleware configuration for RythuSaathi."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .exceptions import register_exception_handlers
from .routes import (
    admin_router,
    advisory_router,
    auth_router,
    configuration_router,
    conversations_router,
    credentials_router,
    history_router,
)


def create_app() -> FastAPI:
    """Create and configure the production-quality local-first FastAPI application."""
    app = FastAPI(
        title="RythuSaathi Agricultural Multi-Agent Advisory API",
        description=(
            "Local-first REST API exposing the seven-agent Microsoft Agent Framework "
            "agricultural advisory system to modern web frontends."
        ),
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # Enable CORS for local-first web clients and future React frontends
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register centralized, diagnostic-safe error handlers
    register_exception_handlers(app)

    # Mount API v1 route groups
    api_v1_prefix = "/api/v1"
    app.include_router(auth_router, prefix=api_v1_prefix)
    app.include_router(credentials_router, prefix=api_v1_prefix)
    app.include_router(advisory_router, prefix=api_v1_prefix)
    app.include_router(configuration_router, prefix=api_v1_prefix)
    app.include_router(conversations_router, prefix=api_v1_prefix)
    app.include_router(history_router, prefix=api_v1_prefix)
    app.include_router(admin_router, prefix=api_v1_prefix)

    @app.get("/api/v1/health", tags=["Health"], summary="System health check")
    async def health_check() -> dict[str, str | int]:
        return {
            "status": "healthy",
            "service": "rythusaathi",
            "agents": 7,
            "version": "0.1.0",
        }

    return app


# Default ASGI application instance for uvicorn
app = create_app()

