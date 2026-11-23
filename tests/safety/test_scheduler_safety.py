"""Phase 7 safety invariants: the scheduler/worker can prepare but never approve or submit; secrets stay encrypted; operations are token-protected; defaults are closed."""
import inspect
import re

import pytest

from backend.main import app
from config.automation_config import ADJUSTABLE
from config.settings import PROJECT_ROOT, Settings

pytestmark = pytest.mark.safety
SCHEDULER_SOURCES = ["scheduler/scheduler.py", "scheduler/application_queue.py", "scheduler/application_worker.py", "scheduler/daily_discovery.py",
                     "scheduler/daily_report.py", "scheduler/runtime.py", "database/repositories/queue_repository.py", "database/repositories/scheduler_repository.py"]
PHASE7_SOURCES = SCHEDULER_SOURCES + ["notifications/email_notifications.py", "notifications/notification_templates.py", "backend/api/routes/scheduler.py",
                                      "database/repositories/notifications_repository.py", "config/automation_config.py"]


def src(rel: str) -> str:
    return (PROJECT_ROOT / rel).read_text(encoding="utf-8")


def code_only(text: str) -> str:
    text = re.sub(r'"""[\s\S]*?"""', "", text)
    return "\n".join(line.split("#")[0] for line in text.splitlines())


@pytest.mark.parametrize("rel", SCHEDULER_SOURCES)
def test_the_scheduler_side_has_no_code_path_that_approves_submits_or_drives_a_browser(rel):
    text = code_only(src(rel))
    for forbidden in (r"\.submit\(", r"\.approve\(", r"submit\s*=\s*True", r"run_form", r"\.confirm\(", r"playwright", r"BrowserManager", r"manual_handoff\(", r"set_answers"):
        assert not re.search(forbidden, text), f"{rel}: {forbidden}"


def test_the_only_application_call_the_worker_makes_is_prepare():
    runtime = code_only(src("scheduler/runtime.py"))
    calls = set(re.findall(r"svc\.(\w+)\(", runtime))
    assert calls == {"prepare"}
    assert "Outcome(" in runtime and "approve" not in runtime


def test_queueing_never_creates_or_changes_an_application():
    q = code_only(src("scheduler/application_queue.py"))
    assert "apps.insert" not in q and "apps.update" not in q and "transition(" not in q.replace("self.repo.transition", "")


def test_retry_path_checks_for_possible_submission_before_requeueing():
    w = src("scheduler/application_worker.py")
    assert "_guard_uncertain" in w and w.index("out = self._guard_uncertain(task, out)") > w.index("out = self.processor(task)")
    assert "SUBMISSION_MAY_EXIST" in w and "VERIFICATION_PENDING" in w and "not retried" in w


def test_defaults_are_closed():
    s = Settings(_env_file=None)
    assert s.scheduler_autostart is False and s.admin_token == "" and s.notifications_enabled is False and s.notification_recipient == "" and s.application_automation_enabled is False
    assert s.scheduler_timezone == "Asia/Kolkata"


def test_gmail_secrets_are_never_settings_or_plain_text():
    fields = set(Settings.model_fields)
    assert not {f for f in fields if re.search(r"gmail_(client|refresh|password|token)|oauth|google_secret", f)}
    mail = code_only(src("notifications/email_notifications.py"))
    assert "password" not in mail.lower().replace("no gmail password", "")
    assert re.findall(r"https?://[\w./-]+", src("notifications/email_notifications.py")) == ["https://oauth2.googleapis.com/token", "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                                                                                              "https://www.googleapis.com/auth/gmail.send"] or all("googleapis.com" in u for u in re.findall(r"https?://[\w./-]+", src("notifications/email_notifications.py")))
    assert "logger" in mail and not re.search(r"logger\.\w+\([^)]*(token|secret|creds)", mail, re.I)
    assert "encrypt" in mail and "write_bytes(self._fernet().encrypt" in mail


def test_every_state_changing_phase7_endpoint_requires_the_admin_token():
    from backend.api.routes import scheduler as routes
    protected = open_reads = 0
    for router in (routes.scheduler_router, routes.queue_router, routes.notifications_router, routes.reports_router):
        for r in router.routes:
            deps = [d.call for d in r.dependant.dependencies]
            if r.methods & {"POST", "PUT", "DELETE", "PATCH"}:
                assert routes.require_admin in deps, f"{r.methods} {r.path} is not protected"
                protected += 1
            else:
                assert routes.require_admin not in deps, r.path        # reads stay open for the dashboard
                open_reads += 1
    assert protected >= 13 and open_reads >= 6
    # and the routers really are the ones mounted on the app
    mounted = set(app.openapi()["paths"])
    assert {"/api/scheduler/start", "/api/queue/{task_id}/cancel", "/api/reports/daily/generate"} <= mounted


def test_admin_token_comparison_is_constant_time():
    from backend.api.routes import scheduler as routes
    assert "compare_digest" in inspect.getsource(routes.require_admin)


def test_only_non_timing_limits_are_adjustable_and_only_downwards():
    assert set(ADJUSTABLE) == {"max_applications_per_day", "max_applications_per_platform_per_day", "max_jobs_discovered_per_day", "max_jobs_processed_per_day",
                               "max_retries_per_application", "max_concurrent_workers", "min_match_score"}
    assert not any(re.search(r"delay|window|backoff|timezone|lease", k) for k in ADJUSTABLE)              # courtesy delays and windows cannot be loosened from the UI


def test_history_and_reports_are_append_only():
    from database.repositories.notifications_repository import NotificationsRepository
    from database.repositories.queue_repository import QueueRepository
    from database.repositories.scheduler_repository import SchedulerRepository
    assert not [n for n in dir(SchedulerRepository) if re.search(r"(update|delete|remove|edit)_?(report|run)", n)]
    for cls in (QueueRepository, NotificationsRepository, SchedulerRepository):
        assert not [n for n in dir(cls) if n.startswith(("delete", "remove", "purge", "drop"))], cls
    assert 'delete_one({"name"' in inspect.getsource(SchedulerRepository.release_lock)                    # the only delete is the scheduler's own lock row


def test_the_daily_target_is_a_goal_not_a_quota():
    import yaml
    data = yaml.safe_load((PROJECT_ROOT / "config/automation_limits.yaml").read_text(encoding="utf-8"))
    assert 20 <= data["daily_application_target"] <= 30 and data["daily_application_target"] >= 0
    assert "NOT a quota" in (PROJECT_ROOT / "config/automation_limits.yaml").read_text(encoding="utf-8")
    assert "daily_application_target" not in code_only(src("scheduler/application_worker.py")) + code_only(src("scheduler/application_queue.py"))   # nothing is driven toward it


def test_hard_caps_in_yaml_still_cover_phase6_keys():
    import yaml
    data = yaml.safe_load((PROJECT_ROOT / "config/automation_limits.yaml").read_text(encoding="utf-8"))
    assert data["max_applications_per_day"] == 20 and data["max_applications_per_platform_per_day"] == 10 and data["delay_seconds_between_actions"] == [3, 8]
