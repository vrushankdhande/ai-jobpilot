"""Dashboard summary and analytics API - read-only, computed from stored records."""
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from backend.services.analytics_service import AnalyticsService, RangeError
from config.settings import Settings, get_settings
from database.connection import DatabaseManager, get_database_manager

dashboard_router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
analytics_router = APIRouter(prefix="/api/analytics", tags=["analytics"])
DATE = r"^\d{4}-\d{2}-\d{2}$"


def get_analytics(manager: DatabaseManager = Depends(get_database_manager), settings: Settings = Depends(get_settings)) -> AnalyticsService:
    return AnalyticsService(manager.get_database(), settings)


@dashboard_router.get("/summary", summary="Dashboard metrics from the stored records")
def summary(days: int = Query(default=14, ge=3, le=90), svc: AnalyticsService = Depends(get_analytics)):
    return svc.dashboard(days)


@analytics_router.get("/overview", summary="Analytics for a date range (default: last 30 days)")
def overview(date_from: str | None = Query(default=None, pattern=DATE), date_to: str | None = Query(default=None, pattern=DATE), svc: AnalyticsService = Depends(get_analytics)):
    try:
        return svc.overview(date_from, date_to)
    except RangeError as exc:
        raise HTTPException(422, str(exc)) from None


@analytics_router.get("/export", summary="Download a report as CSV (daily, weekly, status, sources, scores)")
def export(kind: str = Query(default="daily", max_length=20), date_from: str | None = Query(default=None, pattern=DATE), date_to: str | None = Query(default=None, pattern=DATE),
           svc: AnalyticsService = Depends(get_analytics)):
    try:
        text = svc.export_csv(kind, date_from, date_to)
    except RangeError as exc:
        raise HTTPException(422, str(exc)) from None
    return Response(content=text, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="jobpilot_{kind}.csv"', "X-Content-Type-Options": "nosniff"})
