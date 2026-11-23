"""Root and health endpoints."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from backend.core.config import Settings, get_settings
from backend.core.database import DatabaseManager, get_database_manager

router = APIRouter()


@router.get("/", summary="Welcome message")
def root(settings: Settings = Depends(get_settings)) -> dict:
    return {
        "message": f"Welcome to {settings.app_name} API",
        "version": settings.app_version,
        "docs": "/docs",
    }


@router.get("/health", summary="Application health")
def health(settings: Settings = Depends(get_settings)) -> dict:
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.app_env,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/health/database", summary="MongoDB connectivity")
def health_database(
    manager: DatabaseManager = Depends(get_database_manager),
    settings: Settings = Depends(get_settings),
):
    if manager.ping():
        return {
            "status": "ok",
            "database": "connected",
            "database_name": settings.mongodb_database,
        }
    return JSONResponse(
        status_code=503,
        content={"status": "unavailable", "database": "disconnected"},
    )
