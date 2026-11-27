"""Application execution API (/api/applications). Permission, approval and duplicate rules are enforced in ApplicationService - never only in the UI."""
import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from pydantic import BaseModel, Field

from backend.api.routes.documents import get_document_service
from backend.api.routes.jobs import get_discovery_service
from backend.services.discovery_service import DiscoveryService
from backend.api.security import require_admin_if_configured
from backend.services.application_service import (
    ActionNotAllowed, ApplicationNotFound, ApplicationService, DuplicateApplication, LimitReached, NotEligible,
)
from backend.services.document_service import DocumentNotFound, DocumentService
from backend.services.rejection_service import RejectionNotAllowed, RejectionNotFound, RejectionService
from backend.services.matching_service import JobNotFound, MatchNotFound, NoActiveResume, ProfileMissing, UnreviewedProfile
from browser.screenshot_manager import ScreenshotManager
from browser.session_manager import SessionManager
from config.automation_config import load_limits
from config.settings import Settings, get_settings
from database.repositories.applications_repository import ApplicationsRepository
from engines.application.application_engine import ApplicationEngine
from engines.application.retry_manager import RetryManager
from integrations.application_policy import ApplicationPolicy
from integrations.policy import PolicyRegistry, PolicyViolation

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/applications", tags=["applications"])
HEX32 = r"^[0-9a-f]{32}$"
AppId = Annotated[str, Path(pattern=HEX32, description="32-character hex application id")]   # Annotated: one shared Path() object would take over the first parameter name
JobId = Annotated[str, Path(pattern=HEX32, description="32-character hex job id")]


def get_application_service(docs: DocumentService = Depends(get_document_service), settings: Settings = Depends(get_settings)) -> ApplicationService:
    db = docs.docs._docs.database
    apps = ApplicationsRepository(db)
    limits = load_limits(settings)
    shots = ScreenshotManager(settings.screenshots_path)
    policy = ApplicationPolicy.load(settings, PolicyRegistry.load(settings))
    engine = ApplicationEngine(settings, SessionManager(settings), shots, limits)
    return ApplicationService(apps, docs.matching, docs, settings, policy, engine, RetryManager(apps, settings.max_submit_retries), limits, shots)


# ---- request models -------------------------------------------------------------------------------------------
class PrepareRequest(BaseModel):
    job_id: str = Field(pattern=HEX32)
    resume_id: str | None = Field(default=None, max_length=64)
    allow_unreviewed: bool = False
    acknowledge_ineligible: bool = False
    acknowledge_possible_duplicate: bool = False
    candidate_inputs: dict[str, str | bool] = Field(default_factory=dict, max_length=40,
                                                    description="Your own answers for this application, keyed by the form field's label")


class AnswersRequest(BaseModel):
    answers: dict[str, str | bool] = Field(max_length=40)


class ApproveRequest(BaseModel):
    acknowledge_warnings: bool = False


class NotesRequest(BaseModel):
    notes: str | None = Field(default=None, max_length=2000)


class RejectRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=300)


class RecordAppliedRequest(BaseModel):
    job_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    notes: str | None = Field(default=None, max_length=500)


class AppliedStatesRequest(BaseModel):
    job_ids: list[str] = Field(default_factory=list, max_length=200)


class RejectionConfirmRequest(BaseModel):
    application_id: str = Field(pattern=HEX32)


class ConfirmRequest(BaseModel):
    submitted: bool = True
    reference: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=500)


def _guard(fn):
    """Maps domain errors to clean HTTP errors (no internals, no stack traces)."""
    try:
        return fn()
    except (NoActiveResume, UnreviewedProfile) as exc:
        raise HTTPException(409, str(exc)) from None
    except (ProfileMissing, JobNotFound, MatchNotFound, ApplicationNotFound, DocumentNotFound):
        raise HTTPException(404, "Application, job, document or profile not found.") from None
    except NotEligible as exc:
        raise HTTPException(409, {"code": "not_eligible", "message": str(exc)}) from None
    except DuplicateApplication as exc:
        raise HTTPException(409, {"code": "duplicate_application", "message": str(exc), "existing_application_id": exc.existing_id}) from None
    except ActionNotAllowed as exc:
        raise HTTPException(409, {"code": "not_allowed", "message": str(exc), "checks": exc.checks}) from None
    except PolicyViolation as exc:
        raise HTTPException(403, {"code": "policy", "message": str(exc)}) from None
    except LimitReached as exc:
        raise HTTPException(429, {"code": "limit", "message": str(exc)}) from None


# ---- endpoints ------------------------------------------------------------------------------------------------------
@router.post("/prepare", status_code=201, summary="Prepare an application (documents, form detection, field mapping, validation)")
def prepare(body: PrepareRequest, svc: ApplicationService = Depends(get_application_service)):
    return _guard(lambda: svc.prepare(body.job_id, resume_id=body.resume_id, allow_unreviewed=body.allow_unreviewed,
                                      acknowledge_ineligible=body.acknowledge_ineligible, acknowledge_possible_duplicate=body.acknowledge_possible_duplicate,
                                      candidate_inputs=body.candidate_inputs))


@router.post("/record-applied", summary="Record that YOU applied to this job yourself (nothing is sent; shown as recorded by you)")
def record_applied(body: RecordAppliedRequest, svc: ApplicationService = Depends(get_application_service)):
    return _guard(lambda: svc.record_applied_yourself(body.job_id, notes=body.notes))


@router.post("/undo-applied", summary="Undo your own \"I applied\" record (the Applied switch); never an application JobPilot submitted")
def undo_applied(body: RecordAppliedRequest, svc: ApplicationService = Depends(get_application_service)):
    return _guard(lambda: svc.undo_recorded_applied(body.job_id))


@router.post("/states", summary="Application state per job (for the 'applied' switches)")
def applied_states(body: AppliedStatesRequest, svc: ApplicationService = Depends(get_application_service)):
    return svc.applied_states([j for j in body.job_ids if isinstance(j, str) and len(j) == 32])


@router.get("/autofill/{job_id}", summary="Values to fill an application form in your own browser (verified data only; never submits)")
def autofill(job_id: JobId, svc: ApplicationService = Depends(get_application_service)):
    return _guard(lambda: svc.autofill(job_id))


@router.get("", summary="List applications")
def list_applications(state: str | None = None, job_id: str | None = Query(default=None, pattern=HEX32), source: str | None = Query(default=None, max_length=40),
                      limit: int = Query(default=100, ge=1, le=500), skip: int = Query(default=0, ge=0),
                      svc: ApplicationService = Depends(get_application_service)):
    return svc.list_applications(state=state, job_id=job_id, source=source, limit=limit, skip=skip)


@router.get("/page", summary="Paged, searchable list: {total, items}")
def page_applications(state: str | None = Query(default=None, max_length=40), states: list[str] | None = Query(default=None, max_length=12), q: str | None = Query(default=None, max_length=100), source: str | None = Query(default=None, max_length=40),
                      date_from: datetime | None = None, date_to: datetime | None = None, dismissed: bool | None = None,
                      employer_outcome: str | None = Query(default=None, pattern=r"^(rejected|none)$"),
                      limit: int = Query(default=25, ge=1, le=200), skip: int = Query(default=0, ge=0), svc: ApplicationService = Depends(get_application_service)):
    return svc.page(state=state, states=states, q=q, source=source, date_from=date_from, date_to=date_to, dismissed=dismissed, employer_outcome=employer_outcome,
                    limit=limit, skip=skip)


# ---- rejection tracking (replies in your own Gmail that say "not selected") ----------------------------------------
SignalId = Annotated[str, Path(pattern=HEX32, description="32-character hex id of a rejection e-mail record")]


def get_rejection_service(svc: ApplicationService = Depends(get_application_service), settings: Settings = Depends(get_settings)) -> RejectionService:
    return RejectionService(svc.apps.col.database, settings)


def _rguard(fn):
    try:
        return fn()
    except RejectionNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except RejectionNotAllowed as exc:
        raise HTTPException(409, {"code": "not_allowed", "message": str(exc)}) from None


@router.get("/rejections", summary="Rejection e-mails found in your Gmail: to confirm, unmatched, recently applied; last check")
def rejections(svc: RejectionService = Depends(get_rejection_service)):
    return svc.overview()


@router.post("/rejections/check", dependencies=[Depends(require_admin_if_configured)], summary="Check Gmail for rejection e-mails now (read-only)")
def check_rejections(svc: RejectionService = Depends(get_rejection_service)):
    return svc.scan(trigger="manual")


@router.post("/rejections/{signal_id}/confirm", summary="Yes: this rejection e-mail is about that application (marks it rejected)")
def confirm_rejection(body: RejectionConfirmRequest, signal_id: SignalId, svc: RejectionService = Depends(get_rejection_service)):
    return _rguard(lambda: svc.confirm(signal_id, body.application_id))


@router.post("/rejections/{signal_id}/add-as-applied", summary="You applied to this job outside JobPilot: add it to Applied Jobs and mark it rejected")
def add_rejection_as_applied(signal_id: SignalId, svc: RejectionService = Depends(get_rejection_service),
                             apps: ApplicationService = Depends(get_application_service), discovery: DiscoveryService = Depends(get_discovery_service)):
    def import_job(fields: dict, source: str | None) -> dict | None:
        try:
            return discovery.import_job(None, fields, source).get("job")
        except ValueError as exc:
            raise RejectionNotAllowed(f"The job could not be added: {exc}") from None
    def record_applied(job_id: str, notes: str) -> dict:
        rec = apps.record_applied_yourself(job_id)          # no note here: a note would count as evidence and mark it VERIFIED
        if not rec.get("user_notes"):
            apps.set_notes(rec["application_id"], notes)
        return rec
    return _rguard(lambda: _guard(lambda: svc.add_as_applied(signal_id, import_job=import_job, record_applied=record_applied)))


@router.post("/rejections/{signal_id}/dismiss", summary="No: this e-mail is not about any of my applications")
def dismiss_rejection(signal_id: SignalId, svc: RejectionService = Depends(get_rejection_service)):
    return _rguard(lambda: svc.dismiss(signal_id))


@router.post("/{application_id}/employer-rejected", summary="Mark that the employer rejected this application (your own record)")
def mark_employer_rejected(application_id: AppId, svc: RejectionService = Depends(get_rejection_service)):
    return _rguard(lambda: svc.mark_rejected(application_id))


@router.post("/{application_id}/employer-rejected/undo", summary="Remove the 'rejected by employer' mark (wrong match or mistake)")
def undo_employer_rejected(application_id: AppId, svc: RejectionService = Depends(get_rejection_service)):
    return _rguard(lambda: svc.undo(application_id))


@router.get("/policy/{job_id}", summary="May this job's application be automated? (policy decision + apply link)")
def policy_for_job(job_id: JobId, svc: ApplicationService = Depends(get_application_service)):
    return _guard(lambda: svc.policy_for_job(job_id))


@router.get("/{application_id}", summary="Application details (mapping, validation, approval, handoff package)")
def get_application(application_id: AppId, svc: ApplicationService = Depends(get_application_service)):
    return _guard(lambda: svc.get(application_id))


@router.post("/{application_id}/validate", summary="Re-detect the form, re-map fields and re-validate")
def validate(application_id: AppId, svc: ApplicationService = Depends(get_application_service)):
    return _guard(lambda: svc.validate(application_id))


@router.put("/{application_id}/answers", summary="Provide your own answers for fields that need input (sensitive/unknown questions)")
def set_answers(body: AnswersRequest, application_id: AppId, svc: ApplicationService = Depends(get_application_service)):
    return _guard(lambda: svc.set_answers(application_id, body.answers))


@router.post("/{application_id}/approve", dependencies=[Depends(require_admin_if_configured)], summary="Approve exactly this prepared application (explicit candidate action)")
def approve(body: ApproveRequest, application_id: AppId, svc: ApplicationService = Depends(get_application_service)):
    return _guard(lambda: svc.approve(application_id, acknowledge_warnings=body.acknowledge_warnings))


@router.post("/{application_id}/submit", dependencies=[Depends(require_admin_if_configured)], summary="Submit an APPROVED application on a permitted platform (never on restricted ones)")
def submit(application_id: AppId, svc: ApplicationService = Depends(get_application_service)):
    return _guard(lambda: svc.submit(application_id))


@router.post("/{application_id}/manual-handoff", summary="Switch to manual handoff: you apply yourself with the prepared package")
def manual_handoff(application_id: AppId, svc: ApplicationService = Depends(get_application_service)):
    return _guard(lambda: svc.manual_handoff(application_id))


@router.post("/{application_id}/manual-handoff/opened", summary="Record that you opened the application link")
def manual_opened(application_id: AppId, svc: ApplicationService = Depends(get_application_service)):
    return _guard(lambda: svc.mark_opened(application_id))


@router.post("/{application_id}/confirm", summary="Record the outcome you observed yourself (manual handoff or an uncertain submission)")
def confirm(body: ConfirmRequest, application_id: AppId, svc: ApplicationService = Depends(get_application_service)):
    return _guard(lambda: svc.confirm(application_id, submitted=body.submitted, reference=body.reference, notes=body.notes))


@router.put("/{application_id}/notes", summary="Update your own notes on an application")
def set_notes(body: NotesRequest, application_id: AppId, svc: ApplicationService = Depends(get_application_service)):
    return _guard(lambda: svc.set_notes(application_id, body.notes))


@router.post("/{application_id}/reject", summary="Reject: decide not to apply (before anything is sent)")
def reject(body: RejectRequest, application_id: AppId, svc: ApplicationService = Depends(get_application_service)):
    return _guard(lambda: svc.reject(application_id, body.reason))


@router.get("/{application_id}/status", summary="Submission status and evidence")
def status(application_id: AppId, svc: ApplicationService = Depends(get_application_service)):
    return _guard(lambda: svc.status(application_id))


@router.get("/{application_id}/events", summary="Immutable event history")
def events(application_id: AppId, svc: ApplicationService = Depends(get_application_service)):
    return _guard(lambda: svc.events(application_id))


@router.get("/{application_id}/screenshots/{screenshot_id}", summary="A preview/confirmation screenshot of this application")
def screenshot(application_id: AppId, screenshot_id: str = Path(pattern=r"^[0-9a-f]{12}_[0-9a-f]{16}\.png$"),
               svc: ApplicationService = Depends(get_application_service)):
    data = _guard(lambda: svc.screenshot(application_id, screenshot_id))
    if data is None:
        raise HTTPException(404, "Screenshot not found.")
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

