"""FastAPI application entrypoint."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pymongo.errors import AutoReconnect, ConnectionFailure, PyMongoError

from backend.api.routes import applications, auth as auth_routes, documents, health, jobs, matching, resumes
from backend.api.routes import analytics as analytics_routes, interviews, settings as settings_routes
from backend.api.routes import scheduler as scheduler_routes
from backend.api.security import auth_middleware, hardening_middleware
from backend.core.config import get_settings
from backend.core.database import DatabaseConnectionError, database_manager
from backend.core.logging_config import setup_logging
from config.production_checks import production_problems
from backend.services.auth_service import ensure_auth_indexes
from database.indexes import ensure_indexes

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings)
    logger.info(
        "Starting %s v%s (env=%s)", settings.app_name, settings.app_version, settings.app_env
    )
    problems = production_problems(settings)
    if problems:                                    # fail closed: never run in production with an unsafe configuration
        for p in problems:
            logger.error("Unsafe production configuration: %s", p)
        raise RuntimeError("Refusing to start in production: " + "; ".join(problems))
    database_manager.on_connect = ensure_indexes    # also runs when MongoDB comes up after the API
    try:
        database_manager.connect(settings.mongodb_uri, settings.mongodb_database)
    except DatabaseConnectionError:
        # Keep the API up so /health/database can report the problem.
        logger.error("Starting without a database connection")
    else:
        try:
            ensure_indexes(database_manager.get_database())
            ensure_auth_indexes(database_manager.base_database())
        except Exception:
            logger.exception("Could not ensure MongoDB indexes")
        scheduler_routes.start_all_enabled(database_manager)
    yield
    scheduler_routes.reset_runtime()
    database_manager.close()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    settings = get_settings()
    production = settings.app_env == "production"
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="AI-powered job discovery, matching and application assistant.",
        docs_url=None if production else "/docs",
        redoc_url=None if production else "/redoc",
        openapi_url=None if production else "/openapi.json",
        lifespan=lifespan,
    )
    app.middleware("http")(auth_middleware)
    app.middleware("http")(hardening_middleware)       # added last = runs first (size limit and headers also on 401s)
    app.include_router(health.router)
    app.include_router(auth_routes.router)
    app.include_router(resumes.router)
    app.include_router(matching.router)
    app.include_router(jobs.router)
    app.include_router(documents.router)
    app.include_router(applications.router)
    app.include_router(interviews.router)
    app.include_router(settings_routes.router)
    app.include_router(analytics_routes.dashboard_router)
    app.include_router(analytics_routes.analytics_router)
    for r in (scheduler_routes.scheduler_router, scheduler_routes.queue_router, scheduler_routes.notifications_router, scheduler_routes.reports_router):
        app.include_router(r)

    @app.exception_handler(DatabaseConnectionError)
    async def _db_error_handler(request: Request, exc: DatabaseConnectionError):
        return JSONResponse(status_code=503, content={"detail": "Database unavailable"})

    @app.exception_handler(PyMongoError)
    async def _mongo_error_handler(request: Request, exc: PyMongoError):
        if isinstance(exc, (AutoReconnect, ConnectionFailure)):     # includes server-selection and network timeouts
            logger.error("MongoDB unavailable during %s %s: %s", request.method, request.url.path, type(exc).__name__)
            return JSONResponse(status_code=503, content={"detail": "Database unavailable"})
        logger.exception("Database error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    @app.exception_handler(Exception)
    async def _unhandled_error_handler(request: Request, exc: Exception):
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    return app


app = create_app()
