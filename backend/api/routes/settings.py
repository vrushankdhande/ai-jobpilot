"""Settings API (/api/settings). Reads never include secrets; every write needs the admin token and is validated before saving."""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from backend.api.routes import scheduler as sched_routes
from backend.api.routes.scheduler import require_admin
from backend.services.settings_service import SettingsError, SettingsService
from config.settings import Settings, get_settings
from database.connection import DatabaseManager, get_database_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/settings", tags=["settings"])


def get_settings_service(manager: DatabaseManager = Depends(get_database_manager), settings: Settings = Depends(get_settings)) -> SettingsService:
    return SettingsService(settings, manager.get_database())


class PreferencesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict


class ScoringBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    weights: dict[str, float] | None = None
    thresholds: dict[str, float] | None = None
    recommendation_threshold: float | None = None
    min_data_coverage: float | None = None
    missing_data_policy: str | None = None


class ValuesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict


def _guard(fn):
    try:
        return fn()
    except SettingsError as exc:
        raise HTTPException(422, {"message": "Invalid settings", "problems": exc.problems}) from None
    except OSError:
        logger.error("Settings file could not be written")
        raise HTTPException(500, "The configuration file could not be written.") from None


@router.get("", summary="Current configuration (no secrets)")
def read_settings(svc: SettingsService = Depends(get_settings_service)):
    return svc.read()


@router.put("/preferences", dependencies=[Depends(require_admin)], summary="Save job preferences (config/job_preferences.yaml)")
def save_preferences(body: PreferencesBody, svc: SettingsService = Depends(get_settings_service)):
    return _guard(lambda: svc.update_preferences(body.values))


@router.put("/scoring", dependencies=[Depends(require_admin)], summary="Save matching weights and thresholds (bumps scoring_version)")
def save_scoring(body: ScoringBody, svc: SettingsService = Depends(get_settings_service)):
    return _guard(lambda: svc.update_scoring(body.model_dump(exclude_none=True)))


@router.put("/automation", dependencies=[Depends(require_admin)], summary="Save automation caps, scheduler times and retry settings (config/automation_limits.yaml)")
def save_automation(body: ValuesBody, svc: SettingsService = Depends(get_settings_service), manager: DatabaseManager = Depends(get_database_manager),
                    settings: Settings = Depends(get_settings)):
    out = _guard(lambda: svc.update_automation(body.values))
    sched_routes.reset_runtime()                                   # rebuilt lazily with the new values
    sched_routes.start_if_enabled(manager.get_database(), settings)  # and resumed if it was running
    return out


@router.put("/env", dependencies=[Depends(require_admin)], summary="Save non-secret environment settings (restart required)")
def save_env(body: ValuesBody, svc: SettingsService = Depends(get_settings_service)):
    return _guard(lambda: svc.update_env(body.values))
