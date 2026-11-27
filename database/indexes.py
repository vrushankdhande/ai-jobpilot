"""MongoDB indexes. Idempotent - safe to call on every startup."""
import logging

from pymongo import ASCENDING, DESCENDING
from pymongo.database import Database

logger = logging.getLogger(__name__)


def ensure_indexes(db: Database) -> None:
    db["resumes"].create_index([("resume_id", ASCENDING)], unique=True, name="uq_resume_id")
    db["resumes"].create_index([("sha256", ASCENDING)], name="ix_resume_sha256")
    db["resumes"].create_index([("uploaded_at", DESCENDING)], name="ix_resume_uploaded_at")
    db["candidate_profiles"].create_index([("resume_id", ASCENDING)], unique=True, name="uq_profile_resume")
    db["candidate_profiles"].create_index([("profile_id", ASCENDING)], unique=True, name="uq_profile_id")
    db["profile_versions"].create_index(
        [("resume_id", ASCENDING), ("profile_version", ASCENDING)], unique=True, name="uq_profile_version"
    )
    db["users"].create_index([("user_id", ASCENDING)], unique=True, name="uq_user_id")
    # Phase 3: jobs and matches
    db["jobs"].create_index([("job_id", ASCENDING)], unique=True, name="uq_job_id")
    db["jobs"].create_index([("content_hash", ASCENDING)], unique=True, name="uq_job_content_hash")
    # Phase 5: generated documents
    db["documents"].create_index([("document_id", ASCENDING)], unique=True, name="uq_document_id")
    db["documents"].create_index([("group_id", ASCENDING), ("version", ASCENDING)], unique=True, name="uq_document_group_version")
    db["documents"].create_index([("job_id", ASCENDING), ("document_type", ASCENDING)], name="ix_document_job_type")
    db["documents"].create_index([("resume_id", ASCENDING), ("generated_at", DESCENDING)], name="ix_document_resume_date")
    db["documents"].create_index([("review_status", ASCENDING)], name="ix_document_review")
    # Phase 6: applications (one per resume+job: the unique idempotency key is the duplicate guard) and immutable events
    db["applications"].create_index([("application_id", ASCENDING)], unique=True, name="uq_application_id")
    db["applications"].create_index([("idempotency_key", ASCENDING)], unique=True, name="uq_application_idem")
    db["applications"].create_index([("job_id", ASCENDING)], name="ix_application_job")
    db["applications"].create_index([("state", ASCENDING), ("updated_at", DESCENDING)], name="ix_application_state")
    db["applications"].create_index([("apply_url", ASCENDING)], name="ix_application_url")
    db["application_events"].create_index([("application_id", ASCENDING), ("seq", ASCENDING)], unique=True, name="uq_application_event_seq")
    db["rejection_emails"].create_index([("message_key", ASCENDING)], unique=True, name="uq_rejection_message")   # each e-mail is processed once
    db["rejection_emails"].create_index([("status", ASCENDING), ("received_at", DESCENDING)], name="ix_rejection_status")
    # Phase 7: scheduler, queue, counters, notifications, reports
    db["scheduler_locks"].create_index([("name", ASCENDING)], unique=True, name="uq_lock_name")
    db["scheduler_runs"].create_index([("run_id", ASCENDING)], unique=True, name="uq_sched_run")
    db["scheduler_runs"].create_index([("job", ASCENDING), ("started_at", DESCENDING)], name="ix_sched_run_job")
    db["scheduler_state"].create_index([("key", ASCENDING)], unique=True, name="uq_sched_state")
    db["automation_counters"].create_index([("day", ASCENDING), ("metric", ASCENDING)], unique=True, name="uq_counter_day_metric")
    db["application_queue"].create_index([("task_id", ASCENDING)], unique=True, name="uq_queue_task")
    db["application_queue"].create_index([("idempotency_key", ASCENDING)], unique=True, name="uq_queue_idem")   # one task per resume+job, ever
    db["application_queue"].create_index([("status", ASCENDING), ("priority", DESCENDING), ("next_attempt_at", ASCENDING)], name="ix_queue_claim")
    db["application_queue"].create_index([("application_id", ASCENDING)], name="ix_queue_app")
    db["notifications"].create_index([("notification_id", ASCENDING)], unique=True, name="uq_notification")
    db["notifications"].create_index([("dedupe_key", ASCENDING)], unique=True, sparse=True, name="uq_notification_dedupe")
    db["notifications"].create_index([("created_at", DESCENDING)], name="ix_notification_date")
    db["daily_reports"].create_index([("report_id", ASCENDING)], unique=True, name="uq_report")
    db["daily_reports"].create_index([("date", DESCENDING), ("generated_at", DESCENDING)], name="ix_report_date")
    # Phase 8: interviews (the dup_key makes the same interview impossible to record twice)
    db["interviews"].create_index([("interview_id", ASCENDING)], unique=True, name="uq_interview_id")
    db["interviews"].create_index([("dup_key", ASCENDING)], unique=True, name="uq_interview_dup")
    db["interviews"].create_index([("status", ASCENDING), ("scheduled_at", ASCENDING)], name="ix_interview_status_date")
    db["interviews"].create_index([("application_id", ASCENDING)], name="ix_interview_app")
    # Phase 9: range queries used by the dashboard and analytics (the benchmark showed full collection scans)
    db["jobs"].create_index([("discovered_at", DESCENDING)], name="ix_job_discovered")
    db["applications"].create_index([("created_at", DESCENDING)], name="ix_application_created")
    db["applications"].create_index([("submitted_at", DESCENDING)], name="ix_application_submitted")
    db["application_events"].create_index([("at", DESCENDING)], name="ix_event_at")
    db["scheduler_runs"].create_index([("started_at", DESCENDING)], name="ix_sched_run_started")
    db["job_matches"].create_index([("created_at", DESCENDING)], name="ix_match_created")
    # Phase 4: discovery (documents without these fields are simply not indexed)
    db["jobs"].create_index([("company_key", ASCENDING)], name="ix_job_company_key")
    db["jobs"].create_index([("canonical_url", ASCENDING)], name="ix_job_canonical_url")
    db["jobs"].create_index([("source_refs.canonical_url", ASCENDING)], name="ix_job_ref_url")
    db["jobs"].create_index([("source_refs.source", ASCENDING), ("source_refs.source_job_id", ASCENDING)], name="ix_job_ref_source_id")
    db["jobs"].create_index([("source", ASCENDING), ("discovered_at", DESCENDING)], name="ix_job_source_date")
    db["jobs"].create_index([("posted_date", DESCENDING)], name="ix_job_posted")
    db["jobs"].create_index([("status", ASCENDING)], name="ix_job_status")
    db["jobs"].create_index([("dedup.needs_review", ASCENDING)], name="ix_job_needs_review")
    db["discovery_runs"].create_index([("run_id", ASCENDING)], unique=True, name="uq_run_id")
    db["discovery_runs"].create_index([("started_at", DESCENDING)], name="ix_run_started")
    db["job_matches"].create_index([("match_id", ASCENDING)], unique=True, name="uq_match_id")
    db["job_matches"].create_index([("input_hash", ASCENDING)], name="ix_match_input_hash")  # not unique: re-runs add history
    db["job_matches"].create_index([("resume_id", ASCENDING), ("created_at", DESCENDING)], name="ix_match_resume_date")
    db["job_matches"].create_index([("job_id", ASCENDING)], name="ix_match_job")
    db["job_matches"].create_index([("match_score", DESCENDING)], name="ix_match_score")
    db["job_matches"].create_index([("eligibility_status", ASCENDING)], name="ix_match_eligibility")
    logger.info("MongoDB indexes ensured")
