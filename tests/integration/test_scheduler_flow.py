"""Phase 7 on a throw-away MongoDB with a controllable clock and mocked external services (discovery sources, Gmail, application preparation).

Nothing here contacts a real site, sends a real e-mail or submits a real application.
"""
import re
import threading
from datetime import datetime, timedelta, timezone

import pytest

from ai.schemas.job_schema import JobInput
from config.automation_config import AutomationLimits
from config.settings import get_settings
from database.models.application import ApplicationRecord, SubmissionResult, idempotency_key
from database.repositories.applications_repository import ApplicationsRepository
from database.repositories.queue_repository import QueueRepository
from database.repositories.scheduler_repository import SchedulerRepository
from engines.application.state_machine import AppState
from notifications.email_notifications import GmailAuthError, GmailTemporaryError
from scheduler.application_queue import NotQueueable
from scheduler.application_worker import Outcome
from scheduler.runtime import build_runtime
from scheduler.scheduler import AlreadyRunning, JobSpec, Scheduler, get_zone
from tests.fixtures.jd_dataset import JDS

pytestmark = pytest.mark.integration
UTC = timezone.utc
IST = get_zone("Asia/Kolkata")


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


class FakeTransport:
    def __init__(self):
        self.sent, self.fail = [], None

    def send(self, raw):
        if self.fail:
            raise self.fail
        self.sent.append(raw)
        return f"gm-{len(self.sent)}"


class FakeDiscovery:
    """Stands in for the Phase 4 service: 'discovers' jobs by storing JDs (no network)."""

    def __init__(self, test_db, matching, jds, busy=False, none=False):
        from database.repositories.discovery_runs_repository import DiscoveryRunsRepository
        from database.repositories.jobs_repository import JobsRepository
        self.jobs, self.runs, self.matching, self.jds, self.busy, self.none, self.queries = JobsRepository(test_db), DiscoveryRunsRepository(test_db), matching, jds, busy, none, []

    def start(self, overrides, sources, use_preferences=True):
        from backend.services.discovery_service import DiscoveryBusy
        from integrations.base_adapter import SearchQuery
        self.sources_asked = getattr(self, "sources_asked", []) + [sources]
        if self.busy:
            raise DiscoveryBusy("busy")
        if self.none:
            return None, {"linkedin": "manual only"}, SearchQuery(), []
        run = self.runs.create({}, ["career_pages"], {"linkedin": "manual only"})
        return run, {"linkedin": "manual only"}, SearchQuery(), ["career_pages"]

    def execute(self, run_id, query, sources):
        self.queries.append(query)
        made = 0
        for jd in self.jds[: query.max_jobs or len(self.jds)]:
            self.matching._ensure_job(JobInput(job_description=jd["text"], job_title=jd["title"], company_name=jd["company"], location="Pune, India"))
            made += 1
        self.runs.update(run_id, {"status": "completed", "finished_at": datetime.now(UTC), "totals": {"new": made, "duplicates": 2, "fetched": made + 2, "merged": 1},
                                  "sources": {"career_pages": {"new": made, "fetched": made + 2, "state": "done"}}})


class Env:
    pass


@pytest.fixture
def env(test_db, seeded, make_matching_service, tmp_path):
    e = Env()
    e.db, e.profile, e.matching = test_db, seeded, make_matching_service()
    e.clock = Clock(datetime(2026, 11, 13, 4, 0, tzinfo=UTC))                # 09:30 IST: inside the application window
    e.settings = get_settings().model_copy(update={"notification_credentials_file": str(tmp_path / "g.cred"), "secret_key": "k" * 32, "notifications_enabled": True,
                                                    "notification_recipient": "me@example.com"})
    e.transport, e.calls, e.proc = FakeTransport(), [], {"fn": lambda t: Outcome("awaiting_approval", application_id="a" * 32)}
    e.discovery = FakeDiscovery(test_db, e.matching, JDS[:5])

    def processor(task):
        e.calls.append(task["task_id"])
        return e.proc["fn"](task)
    e.build = lambda **kw: build_runtime(test_db, e.settings, now_fn=e.clock, processor=processor, transport=e.transport, discovery_service=kw.get("discovery", e.discovery))
    e.rt = e.build()
    e.apps, e.queue, e.sched = ApplicationsRepository(test_db), QueueRepository(test_db), SchedulerRepository(test_db)
    e.enqueue = lambda job="j1" * 16, score=88.0, elig="PASS", resume=None, **kw: e.rt.queue.enqueue(job_id=job, resume_id=resume or seeded.resume_id, match_score=score, eligibility=elig, **kw)
    return e


def add_app(e, job_id, state, resume=None, outcome="not_attempted", **kw):
    rec = ApplicationRecord(idempotency_key=idempotency_key(resume or e.profile.resume_id, job_id), job_id=job_id, resume_id=resume or e.profile.resume_id, profile_id="p" * 32,
                            profile_version=1, source="local_test", state=state, submission=SubmissionResult(outcome=outcome), **kw)
    return e.apps.insert(rec)


# ============================================================================================ scheduler
def make_sched(e, fn, at="07:00", **kw):
    return Scheduler(e.sched, IST, [JobSpec("daily", fn, at=at, lock_ttl=60, **kw)], now_fn=e.clock, catchup_grace_minutes=240)


def test_daily_job_runs_once_at_its_local_time_and_records_history(env):
    ran = []
    s = make_sched(env, lambda t: ran.append(t) or {"n": 1})
    s.start(background=False)
    env.clock.t = datetime(2026, 11, 13, 1, 0, tzinfo=UTC)                   # 06:30 IST: not yet
    assert s.tick() == [] and ran == []
    env.clock.t = datetime(2026, 11, 13, 1, 31, tzinfo=UTC)                  # 07:01 IST
    out = s.tick()
    assert [r["job"] for r in out] == ["daily"] and ran == ["scheduled"]
    env.clock.advance(minutes=30)
    assert s.tick() == [] and ran == ["scheduled"]                           # not twice for the same slot
    env.clock.t = datetime(2026, 11, 14, 1, 31, tzinfo=UTC)                  # next day
    assert len(s.tick()) == 1 and len(ran) == 2
    hist = env.sched.list_runs("daily")
    assert [h["status"] for h in hist] == ["completed", "completed"] and hist[0]["trigger"] == "scheduled" and hist[0]["result"] == {"n": 1} and hist[0]["finished_at"]
    st = s.status()["jobs"][0]
    assert st["last_status"] == "completed" and st["next_run_local"].startswith("2026-11-15T07:00") and not st["running"]


def test_nothing_runs_while_stopped_or_paused_and_resume_works(env):
    ran = []
    s = make_sched(env, lambda t: ran.append(1) or {})
    env.clock.t = datetime(2026, 11, 13, 1, 31, tzinfo=UTC)
    assert s.tick() == []                                                    # never started
    s.start(background=False)
    s.pause()
    assert s.tick() == [] and s.status()["paused"] is True
    s.resume()
    assert len(s.tick()) == 1
    env.clock.t = datetime(2026, 11, 14, 1, 31, tzinfo=UTC)
    s.stop()
    assert s.tick() == [] and ran == [1] and s.status()["enabled"] is False


def test_restart_recovery_catches_up_within_grace_and_never_replays_stale_slots(env):
    ran = []
    s1 = make_sched(env, lambda t: ran.append(1) or {})
    s1.start(background=False)
    env.clock.t = datetime(2026, 11, 13, 1, 31, tzinfo=UTC)
    s1.tick()
    assert len(ran) == 1
    s1.shutdown()                                                            # process "dies"; persisted state remains
    s2 = make_sched(env, lambda t: ran.append(1) or {})                      # a NEW process
    assert s2.status()["enabled"] is True                                    # persisted enabled flag survives
    env.clock.t = datetime(2026, 11, 14, 4, 0, tzinfo=UTC)                   # restarted 09:30 IST next day: 2.5h after the 07:00 slot
    assert len(s2.tick()) == 1 and len(ran) == 2                             # missed slot made up exactly once
    s3 = make_sched(env, lambda t: ran.append(1) or {})
    env.clock.t = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)                  # down for days: 3 missed slots, last one > grace old
    assert s3.tick() == [] and len(ran) == 2                                 # no stale burst
    env.clock.t = datetime(2026, 11, 17, 1, 31, tzinfo=UTC)
    assert len(s3.tick()) == 1 and len(ran) == 3                             # resumes with the next regular slot


def test_runs_orphaned_by_a_crash_are_marked_interrupted(env):
    old = env.sched.create_run("daily", "scheduled", env.clock() - timedelta(hours=3))
    recent = env.sched.create_run("daily", "scheduled", env.clock())
    s = make_sched(env, lambda t: {})
    s.start(background=False)
    by_id = {r["run_id"]: r for r in env.sched.list_runs()}
    assert by_id[old["run_id"]]["status"] == "interrupted" and "restart" in by_id[old["run_id"]]["error"] and by_id[recent["run_id"]]["status"] == "running"


def test_overlapping_executions_are_prevented_across_scheduler_instances(env):
    gate, started, results = threading.Event(), threading.Event(), []

    def slow(trigger):
        started.set()
        gate.wait(5)
        return {"done": True}
    a, b = make_sched(env, slow), make_sched(env, lambda t: {"second": True})
    t = threading.Thread(target=lambda: results.append(a.run_now("daily")))
    t.start()
    assert started.wait(5)
    with pytest.raises(AlreadyRunning):
        b.run_now("daily")                                                   # another process/instance cannot start the same job
    assert b.status()["jobs"][0]["running"] is True
    b.start(background=False)
    env.clock.t = datetime(2026, 11, 13, 1, 31, tzinfo=UTC)
    assert b.tick() == []                                                    # the scheduled run is skipped while the first is running
    gate.set()
    t.join()
    assert results[0]["status"] == "completed" and len(env.sched.list_runs("daily")) == 1
    assert b.run_now("daily")["result"] == {"second": True}                  # free again afterwards
    assert env.sched.lock_holder("job:daily") is None


def test_a_stale_lock_from_a_crashed_process_does_not_block_forever(env):
    assert env.sched.acquire_lock("job:daily", "dead-process", 60, env.clock() - timedelta(minutes=5))      # expired lock left behind
    assert make_sched(env, lambda t: {"ok": 1}).run_now("daily")["status"] == "completed"


def test_a_failing_job_is_recorded_and_does_not_stop_others(env):
    ok = []
    s = Scheduler(env.sched, IST, [JobSpec("bad", lambda t: 1 / 0, at="07:00"), JobSpec("good", lambda t: ok.append(1) or {}, at="07:00")], now_fn=env.clock)
    s.start(background=False)
    env.clock.t = datetime(2026, 11, 13, 1, 31, tzinfo=UTC)
    out = s.tick()
    assert [(r["job"], r["status"]) for r in out] == [("bad", "failed"), ("good", "completed")] and ok == [1]
    bad = env.sched.latest_run("bad")
    assert bad["status"] == "failed" and bad["error"] == "ZeroDivisionError" and s.tick() == []          # not retried in a hot loop


def test_gated_jobs_are_skipped_outside_their_window_and_manual_runs_ignore_the_gate(env):
    s = make_sched(env, lambda t: {"ran": t}, gate=lambda now: "outside the window")
    s.start(background=False)
    env.clock.t = datetime(2026, 11, 13, 1, 31, tzinfo=UTC)
    assert s.tick() == [] and env.sched.list_runs("daily") == []
    assert s.run_now("daily")["result"] == {"ran": "manual"}


def test_background_thread_runs_and_stops_cleanly(env):
    hits = []
    s = Scheduler(env.sched, IST, [JobSpec("fast", lambda t: hits.append(1) or {}, every_seconds=0.05, lock_ttl=5)], now_fn=lambda: datetime.now(UTC), tick_seconds=0.05)
    s.start()
    deadline = datetime.now(UTC) + timedelta(seconds=5)
    while not hits and datetime.now(UTC) < deadline:
        threading.Event().wait(0.05)
    assert hits and s.alive
    s.stop()
    n = len(hits)
    threading.Event().wait(0.3)
    assert not s.alive and len(hits) == n and env.sched.get_state()["enabled"] is False


# ============================================================================================ counters
def test_daily_counters_are_atomic_and_never_exceed_the_limit(env):
    ok = []
    ts = [threading.Thread(target=lambda: ok.append(env.sched.consume("2026-11-13", "jobs_processed", 1, 5))) for _ in range(30)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert ok.count(True) == 5 and env.sched.counts("2026-11-13")["jobs_processed"] == 5
    assert env.sched.consume("2026-11-13", "jobs_processed", 1, 5) is False and env.sched.consume("2026-11-14", "jobs_processed", 1, 5) is True     # new day, new budget
    assert env.sched.consume("2026-11-13", "x", 3, 2) is False and "x" not in env.sched.counts("2026-11-13") and env.sched.consume("2026-11-13", "y", 0, 0)


# ============================================================================================ queue
def test_queue_prevents_duplicates_and_orders_by_priority(env):
    t1, c1 = env.enqueue("a" * 32, 80, "UNKNOWN")
    t2, c2 = env.enqueue("b" * 32, 82, "PASS")
    t3, c3 = env.enqueue("c" * 32, 95, "UNKNOWN")
    again, created = env.enqueue("a" * 32, 99, "PASS")
    assert (c1, c2, c3, created) == (True, True, True, False) and again["task_id"] == t1["task_id"] and again["priority"] == t1["priority"]       # untouched
    assert env.db["application_queue"].count_documents({}) == 3
    order = [env.queue.claim_next("w", 60, env.clock(), max_concurrent=10)["job_id"] for _ in range(3)]
    assert order == ["c" * 32, "b" * 32, "a" * 32] and env.queue.claim_next("w", 60, env.clock(), 10) is None   # 95 > 82+10 (PASS bonus) > 80


def test_priority_order_matches_score_plus_eligibility_bonus(env):
    env.enqueue("a" * 32, 80, "UNKNOWN")       # 80
    env.enqueue("b" * 32, 82, "PASS")          # 92
    env.enqueue("c" * 32, 90, "UNKNOWN")       # 90
    assert [env.queue.claim_next("w", 60, env.clock(), 10)["job_id"][0] for _ in range(3)] == ["b", "c", "a"]


def test_queue_rejects_low_scores_failed_eligibility_and_existing_submissions(env):
    with pytest.raises(NotQueueable) as e1:
        env.enqueue("a" * 32, 60, "PASS")
    assert "below" in str(e1.value)
    with pytest.raises(NotQueueable):
        env.enqueue("b" * 32, 99, "FAIL")
    add_app(env, "c" * 32, "VERIFICATION_PENDING")
    with pytest.raises(NotQueueable) as e2:
        env.enqueue("c" * 32, 99, "PASS")
    assert "already exists" in str(e2.value) and env.db["application_queue"].count_documents({}) == 0
    add_app(env, "d" * 32, "READY_FOR_REVIEW")
    assert env.enqueue("d" * 32, 90, "PASS")[1] is True                      # a draft application does not block queueing


def test_queue_survives_a_restart_and_recovers_tasks_stuck_in_processing(env):
    t, _ = env.enqueue("a" * 32, 90, "PASS")
    env.enqueue("b" * 32, 85, "PASS")
    claimed = env.queue.claim_next("worker-1", 60, env.clock(), 5)                         # worker takes it ... and the process dies
    assert claimed["status"] == "processing" and claimed["attempts"] == 1
    rt2 = env.build()                                                                      # "restart": brand new runtime on the same database
    env.clock.advance(seconds=30)
    assert rt2.queue_repo.recover_stale(env.clock()) == []                                 # lease still valid: someone may be working
    env.clock.advance(seconds=61)
    rec = rt2.queue_repo.recover_stale(env.clock())
    assert [r["task_id"] for r in rec] == [claimed["task_id"]] and rec[0]["status"] == "queued" and rec[0]["last_error"] == "worker stopped unexpectedly"
    assert [h["event"] for h in rt2.queue_repo.get(claimed["task_id"])["history"]] == ["queued", "claimed", "recovered"]
    assert len(rt2.queue_repo.find_all()) == 2 and rt2.queue_repo.counts()["queued"] == 2


def test_claim_respects_the_concurrency_cap_and_due_time(env):
    for i in "abc":
        env.enqueue(i * 32, 90, "PASS")
    assert env.queue.claim_next("w1", 60, env.clock(), max_concurrent=2) and env.queue.claim_next("w2", 60, env.clock(), max_concurrent=2)
    assert env.queue.claim_next("w3", 60, env.clock(), max_concurrent=2) is None
    t = env.queue.claim_next("w3", 60, env.clock(), max_concurrent=3)
    env.queue.transition(t["task_id"], ["processing"], "queued", {"next_attempt_at": env.clock() + timedelta(hours=1)})
    assert env.queue.claim_next("w4", 60, env.clock(), 9) is None                         # not due yet


def test_concurrent_workers_never_claim_the_same_task(env):
    for i in range(12):
        env.enqueue(f"{i:032x}", 90, "PASS")
    got = []
    ts = [threading.Thread(target=lambda n=n: got.extend(t["task_id"] for t in iter(lambda: env.queue.claim_next(f"w{n}", 60, env.clock(), 99), None))) for n in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(got) == 12 == len(set(got))


def test_pause_resume_cancel(env):
    t, _ = env.enqueue("a" * 32, 90, "PASS")
    q = env.rt.queue
    assert q.pause(t["task_id"])["status"] == "paused" and env.queue.claim_next("w", 60, env.clock(), 5) is None      # paused tasks are not processed
    assert q.pause(t["task_id"]) is None                                                                       # only queued tasks can be paused
    assert q.resume(t["task_id"])["status"] == "queued" and env.queue.claim_next("w", 60, env.clock(), 5)["task_id"] == t["task_id"]
    assert q.cancel(t["task_id"])["status"] == "cancelled"                                                     # even while processing
    assert q.cancel(t["task_id"]) is None and q.resume(t["task_id"]) is None and q.pause("0" * 32) is None
    assert [h["event"] for h in env.queue.get(t["task_id"])["history"]] == ["queued", "paused", "resumed", "claimed", "cancelled"]


def test_a_task_whose_application_may_be_submitted_cannot_be_cancelled(env):
    t, _ = env.enqueue("a" * 32, 90, "PASS")
    for state in ("SUBMITTING", "SUBMITTED", "VERIFICATION_PENDING", "VERIFIED"):
        env.db["applications"].delete_many({})
        add_app(env, "a" * 32, state)
        with pytest.raises(NotQueueable):
            env.rt.queue.cancel(t["task_id"])
    assert env.queue.get(t["task_id"])["status"] == "queued"


# ============================================================================================ worker
def run_worker(e):
    return e.rt.worker.run_once("w-test")


def test_worker_prepares_and_marks_awaiting_approval_without_submitting(env):
    t, _ = env.enqueue("a" * 32, 90, "PASS", job_title="ML Engineer", company_name="Northwind")
    out = run_worker(env)
    task = env.queue.get(t["task_id"])
    assert out["processed"][0]["status"] == "awaiting_approval" and task["status"] == "awaiting_approval" and task["application_id"] == "a" * 32 and task["worker_id"] is None
    assert env.calls == [t["task_id"]] and env.sched.counts("2026-11-13")["jobs_processed"] == 1
    assert [h["event"] for h in task["history"]] == ["queued", "claimed", "attempt"]
    assert run_worker(env)["processed"] == [] and len(env.calls) == 1                           # nothing left; the task is not re-prepared
    sent = [n for n in env.rt.notifier.history() if n["type"] == "approval_required"]
    assert len(sent) == 1 and sent[0]["status"] == "sent" and "Nothing is sent" in env.transport.sent[0].decode()


def test_permanent_failures_are_not_retried(env):
    from backend.services.application_service import NotEligible
    env.proc["fn"] = lambda t: (_ for _ in ()).throw(NotEligible("failed eligibility"))
    t, _ = env.enqueue("a" * 32, 90, "PASS")
    run_worker(env)
    task = env.queue.get(t["task_id"])
    assert task["status"] == "failed" and task["last_error"] == "NotEligible" and len(env.calls) == 1
    assert "permanent" in task["history"][-1]["detail"]
    env.clock.advance(hours=5)
    assert run_worker(env)["processed"] == [] and len(env.calls) == 1


def test_transient_failures_retry_with_backoff_then_give_up_and_every_attempt_is_recorded(env):
    env.proc["fn"] = lambda t: (_ for _ in ()).throw(ConnectionError("network down"))
    t, _ = env.enqueue("a" * 32, 90, "PASS")                                                   # max_retries_per_application=3 -> up to 4 attempts
    delays = []
    for attempt in range(1, 5):
        out = run_worker(env)
        task = env.queue.get(t["task_id"])
        assert len(out["processed"]) == 1 and task["attempts"] == attempt
        if task["status"] == "queued":
            delays.append((task["next_attempt_at"].replace(tzinfo=UTC) - env.clock()).total_seconds())
            assert run_worker(env)["processed"] == []                                         # backoff: not due yet
            env.clock.advance(seconds=delays[-1] + 1)
    assert task["status"] == "failed" and task["attempts"] == 4 and "retry limit reached" in task["history"][-1]["detail"]
    assert delays == [60, 120, 240] and len(env.calls) == 4
    assert [h["event"] for h in task["history"]].count("attempt") == 4 and task["last_error"] == "ConnectionError"
    failed = [n for n in env.rt.notifier.history() if n["type"] == "application_failed"]
    assert len(failed) == 1


def test_unexpected_errors_are_recoverable_and_recorded(env):
    state = {"n": 0}

    def flaky(task):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("worker hiccup")
        return Outcome("manual_handoff", application_id="b" * 32)
    env.proc["fn"] = flaky
    t, _ = env.enqueue("a" * 32, 90, "PASS")
    run_worker(env)
    assert env.queue.get(t["task_id"])["status"] == "queued"
    env.clock.advance(seconds=61)
    run_worker(env)
    assert env.queue.get(t["task_id"])["status"] == "manual_handoff" and state["n"] == 2


@pytest.mark.parametrize("state,expected", [("SUBMITTING", "needs_verification"), ("VERIFICATION_PENDING", "needs_verification"), ("SUBMITTED", "completed"), ("VERIFIED", "completed")])
def test_a_retry_can_never_follow_a_possibly_submitted_application(env, state, expected):
    env.proc["fn"] = lambda t: (_ for _ in ()).throw(ConnectionError("dropped after the click"))
    t, _ = env.enqueue("a" * 32, 90, "PASS")
    add_app(env, "a" * 32, state, outcome="unconfirmed")
    run_worker(env)
    task = env.queue.get(t["task_id"])
    assert task["status"] == expected and "may already have been submitted" in task["history"][-1]["detail"]
    env.clock.advance(days=1)
    assert run_worker(env)["processed"] == [] and len(env.calls) == 1                         # processor never called a second time
    if expected == "needs_verification":
        assert any(n["type"] == "verification_needed" for n in env.rt.notifier.history())


def test_daily_processing_limit_defers_extra_tasks_without_losing_them(env):
    env.sched.set_state({"limit_overrides": {"max_jobs_processed_per_day": 2}})
    ids = [env.enqueue(c * 32, 90 - i, "PASS")[0]["task_id"] for i, c in enumerate("abcd")]
    out = run_worker(env)
    assert len(out["processed"]) == 1                                                        # max_concurrent=1 -> one task per pass
    for _ in range(4):
        out = run_worker(env)
    statuses = [env.queue.get(i)["status"] for i in ids]
    assert statuses.count("awaiting_approval") == 2 and statuses.count("queued") == 2 and len(env.calls) == 2
    assert out["skipped_reason"] == "daily processing limit reached" and env.sched.counts("2026-11-13")["jobs_processed"] == 2
    queued = [env.queue.get(i) for i in ids if env.queue.get(i)["status"] == "queued"]
    assert all(q["attempts"] == 0 for q in queued)                                           # a deferral is not an attempt
    env.clock.advance(days=1)                                                                # a new local day: fresh budget
    run_worker(env)
    assert len(env.calls) == 3


def test_worker_respects_time_window_and_global_pause(env):
    t, _ = env.enqueue("a" * 32, 90, "PASS")
    env.clock.t = datetime(2026, 11, 13, 17, 0, tzinfo=UTC)                                  # 22:30 IST: outside 09:00-21:00
    assert "window" in run_worker(env)["skipped_reason"] and env.calls == []
    env.clock.t = datetime(2026, 11, 13, 4, 0, tzinfo=UTC)
    env.rt.scheduler.pause()
    assert "paused" in run_worker(env)["skipped_reason"] and env.calls == []
    env.rt.scheduler.resume()
    assert run_worker(env)["processed"][0]["status"] == "awaiting_approval"


def test_cancelling_while_processing_wins_over_the_workers_result(env):
    t, _ = env.enqueue("a" * 32, 90, "PASS")

    def cancel_midway(task):
        env.rt.queue.cancel(task["task_id"])
        return Outcome("awaiting_approval", application_id="a" * 32)
    env.proc["fn"] = cancel_midway
    out = run_worker(env)
    assert out["processed"][0]["status"] == "superseded" and env.queue.get(t["task_id"])["status"] == "cancelled"


def test_queue_follows_the_applications_real_state(env):
    t, _ = env.enqueue("a" * 32, 90, "PASS", job_title="ML Engineer", company_name="Northwind")
    app = add_app(env, "a" * 32, "READY_FOR_REVIEW")
    env.proc["fn"] = lambda task: Outcome("awaiting_approval", application_id=app["application_id"])
    run_worker(env)
    assert env.queue.get(t["task_id"])["status"] == "awaiting_approval"
    steps = [("VERIFICATION_PENDING", "needs_verification"), ("SUBMITTED", "completed")]
    env.apps.update(app["application_id"], {"state": "APPROVED"})
    assert env.rt.worker.sync_all() == 0 and env.queue.get(t["task_id"])["status"] == "awaiting_approval"
    env.apps.update(app["application_id"], {"state": steps[0][0]})
    assert env.rt.worker.sync_all() == 1 and env.queue.get(t["task_id"])["status"] == "needs_verification"
    env.apps.update(app["application_id"], {"state": "SUBMITTED", "submission": {"outcome": "confirmed", "reference": "R-1"}})
    assert env.rt.worker.sync_all() == 1 and env.queue.get(t["task_id"])["status"] == "completed"
    types = [n["type"] for n in env.rt.notifier.history()]
    assert {"approval_required", "verification_needed", "application_success"} <= set(types)
    ok = [n for n in env.rt.notifier.history() if n["type"] == "application_success"]
    assert len(ok) == 1 and any(b"R-1" in m for m in env.transport.sent)


def test_a_self_reported_submission_is_not_announced_as_a_confirmed_success(env):
    t, _ = env.enqueue("a" * 32, 90, "PASS")
    app = add_app(env, "a" * 32, "MANUAL_HANDOFF_REQUIRED")
    env.proc["fn"] = lambda task: Outcome("manual_handoff", application_id=app["application_id"])
    run_worker(env)
    env.apps.update(app["application_id"], {"state": "SUBMITTED", "submission": {"outcome": "user_reported", "verified_by": "user"}})
    env.rt.worker.sync_all()
    assert env.queue.get(t["task_id"])["status"] == "completed" and not any(n["type"] == "application_success" for n in env.rt.notifier.history())


def test_a_notification_failure_never_breaks_the_queue(env):
    env.transport.fail = GmailTemporaryError("gmail down")
    t, _ = env.enqueue("a" * 32, 90, "PASS")
    assert run_worker(env)["processed"][0]["status"] == "awaiting_approval" and env.queue.get(t["task_id"])["status"] == "awaiting_approval"
    n = env.rt.notifier.history()[0]
    assert n["status"] == "failed" and "temporary" in n["error"] and n["attempts"] == 1


# ============================================================================================ discovery
def test_daily_discovery_matches_and_queues_only_eligible_good_jobs(env):
    out = env.rt.discovery.run("manual")
    assert out["status"] == "completed" and out["new"] == 5 and out["duplicates"] == 2 and out["matched"] == 5 and out["skipped_sources"] == {"linkedin": "manual only"}
    assert out["queued"] + out["not_queued"] == 5 and out["queued"] >= 0
    queued = env.queue.find_all()
    assert len(queued) == out["queued"] and all(t["match_score"] >= 75 and t["eligibility"] != "FAIL" and t["status"] == "queued" for t in queued)
    assert env.sched.counts("2026-11-13")["jobs_discovered"] == 5 and env.db["applications"].count_documents({}) == 0       # discovering never creates applications
    assert any(n["type"] == "discovery_summary" for n in env.rt.notifier.history())
    assert env.rt.discovery.run("manual")["queued"] == 0                                                                      # idempotent: the same jobs are not queued twice
    assert env.db["application_queue"].count_documents({}) == len(queued)


def test_discovery_respects_the_daily_cap_and_passes_the_remainder_to_the_query(env):
    env.sched.set_state({"limit_overrides": {"max_jobs_discovered_per_day": 3}})
    out = env.rt.discovery.run("manual")
    assert out["new"] == 3 and env.discovery.queries[-1].max_jobs == 3
    again = env.rt.discovery.run("manual")
    assert again["status"] == "skipped" and "limit" in again["notes"][0] and len(env.discovery.queries) == 1


def test_discovery_overlap_nothing_to_run_and_missing_profile_are_reported_not_fatal(env):
    assert env.build(discovery=FakeDiscovery(env.db, env.matching, JDS[:1], busy=True)).discovery.run("manual")["notes"] == ["a discovery run is already in progress"]
    none = env.build(discovery=FakeDiscovery(env.db, env.matching, JDS[:1], none=True)).discovery.run("manual")
    assert none["status"] == "nothing_to_run" and "manual import" in none["notes"][0]
    env.db["candidate_profiles"].update_one({"resume_id": env.profile.resume_id}, {"$set": {"review_status": "pending_review"}})
    out = env.rt.discovery.run("manual")
    assert out["status"] == "completed" and out["new"] == 5 and out["queued"] == 0 and "matching skipped" in out["notes"][0]


def test_raising_the_minimum_score_from_the_dashboard_applies_to_new_enqueues(env):
    env.sched.set_state({"limit_overrides": {"min_match_score": 99}})
    with pytest.raises(NotQueueable):
        env.enqueue("a" * 32, 90, "PASS")
    env.sched.set_state({"limit_overrides": {}})
    assert env.enqueue("a" * 32, 90, "PASS")[1]


# ============================================================================================ notifications
def test_not_configured_means_skipped_records_and_a_normal_return(env, tmp_path):
    off = get_settings().model_copy(update={"notifications_enabled": False, "notification_credentials_file": str(tmp_path / "x.cred")})
    rt = build_runtime(env.db, off, now_fn=env.clock, processor=lambda t: Outcome("awaiting_approval"), discovery_service=env.discovery)
    n = rt.notifier.notify("test", "Hello", "body", dedupe_key="t1")
    assert n["status"] == "skipped" and "not configured" in n["error"] and rt.notifier.status()["configured"] is False
    assert rt.notifier.notify("test", "Hello", "body", dedupe_key="t1")["notification_id"] == n["notification_id"]            # deduplicated
    assert rt.notifier.retry_failed() == {"retried": 0, "sent": 0, "failed": 0}
    t, _ = rt.queue.enqueue(job_id="a" * 32, resume_id=env.profile.resume_id, match_score=90, eligibility="PASS")
    assert rt.worker.run_once("w")["processed"][0]["status"] == "awaiting_approval"                                         # the pipeline is unaffected


def test_delivery_status_tracking_dedupe_and_retry_of_temporary_failures(env):
    n = env.rt.notifier
    ok = n.notify("test", "One", "body one", dedupe_key="k1")
    assert ok["status"] == "sent" and ok["attempts"] == 1 and ok["provider_message_id"] == "gm-1" and ok["recipient"] == "m***@example.com" and ok["sent_at"]
    assert n.notify("test", "One", "body one", dedupe_key="k1")["notification_id"] == ok["notification_id"] and len(env.transport.sent) == 1     # never mailed twice
    env.transport.fail = GmailTemporaryError("503")
    bad = n.notify("test", "Two", "body two", dedupe_key="k2")
    assert bad["status"] == "failed" and bad["attempts"] == 1 and "temporary" in bad["error"]
    env.transport.fail = None
    res = n.retry_failed()
    assert res == {"retried": 1, "sent": 1, "failed": 0} and env.db["notifications"].find_one({"notification_id": bad["notification_id"]})["status"] == "sent"
    assert env.db["notifications"].find_one({"notification_id": bad["notification_id"]})["payload"] == {}                                      # body dropped after sending


def test_authentication_failures_are_not_retried_and_retries_are_bounded(env):
    n = env.rt.notifier
    env.transport.fail = GmailAuthError("revoked")
    a = n.notify("test", "Auth", "b", dedupe_key="a1")
    assert a["status"] == "failed" and a["error"].startswith("authentication") and n.retry_failed()["retried"] == 0
    env.transport.fail = GmailTemporaryError("down")
    t = n.notify("test", "Temp", "b", dedupe_key="t1")
    for _ in range(5):
        n.retry_failed()
    assert env.db["notifications"].find_one({"notification_id": t["notification_id"]})["attempts"] == 3                      # MAX_ATTEMPTS, then it stops


def test_history_never_stores_the_body_of_sent_mail_or_any_secret(env):
    env.rt.notifier.notify("test", "Subject", "Secret body text", dedupe_key="s1")
    rows = env.rt.notifier.history()
    assert "Secret body text" not in str(rows) and "k" * 32 not in str(rows) and rows[0]["recipient"] == "m***@example.com" and "me@example.com" not in str(rows)


# ============================================================================================ daily report
def test_daily_report_counts_only_evidence_backed_submissions(env):
    day_start = datetime(2026, 11, 13, 0, 0, tzinfo=IST).astimezone(UTC)
    inside = day_start + timedelta(hours=6)
    yesterday = day_start - timedelta(hours=3)
    add_app(env, "a1" * 16, "READY_FOR_REVIEW")
    add_app(env, "a2" * 16, "APPROVED")
    add_app(env, "a3" * 16, "AWAITING_USER_INPUT")
    add_app(env, "b1" * 16, "SUBMITTED", outcome="confirmed", submitted_at=inside, mode="automated")
    add_app(env, "b2" * 16, "VERIFIED", outcome="confirmed", submitted_at=inside, verified_at=inside, mode="automated")
    add_app(env, "b3" * 16, "SUBMITTED", outcome="user_reported", submitted_at=inside)
    add_app(env, "b4" * 16, "VERIFIED", outcome="confirmed", submitted_at=yesterday, verified_at=yesterday)                  # not today
    add_app(env, "c1" * 16, "VERIFICATION_PENDING", outcome="unconfirmed", submitted_at=inside, mode="automated")                               # attempted, NOT submitted
    add_app(env, "c2" * 16, "SUBMISSION_FAILED")
    add_app(env, "c3" * 16, "MANUAL_HANDOFF_REQUIRED")
    add_app(env, "c4" * 16, "SUBMITTING", submitted_at=inside, mode="automated")
    env.db["application_queue"].insert_one({"task_id": "t" * 32, "idempotency_key": "zz", "status": "failed", "updated_at": inside})
    env.db["applications"].update_many({}, {"$set": {"updated_at": inside}})                # the repository stamps the real "now"; pin the test day
    env.db["jobs"].update_many({}, {"$set": {"discovered_at": inside}})
    env.rt.discovery.run("manual")
    env.db["discovery_runs"].update_many({}, {"$set": {"started_at": inside}})              # runs are stamped with the real "now"; pin the test day
    env.db["jobs"].update_many({}, {"$set": {"discovered_at": inside}})
    env.db["job_matches"].update_many({}, {"$set": {"created_at": inside}})
    rep = env.rt.report.build("2026-11-13")
    a = rep["applications"]
    assert (a["submitted"], a["submitted_self_reported"], a["verified"]) == (2, 1, 1)                                  # b1,b2 confirmed; b3 self-reported; c1/c4 attempts not counted
    assert a["pending_verification"] == 1 and a["manual_handoff"] == 1 and a["awaiting_approval"] == 3 and a["failed_applications"] == 1 and a["failed_tasks"] == 1 and a["failed"] == 2
    assert "not counted as submitted" in a["note"]
    assert rep["discovery"]["discovered"] == 5 and rep["discovery"]["by_source"] and rep["discovery"]["duplicates_removed"] == 2
    m = rep["matching"]
    assert m["matched"] == 5 and m["excellent"] + m["strong"] + m["good"] <= 5 and m["eligible"] <= 5
    assert rep["limits"]["jobs_discovered"] == {"used": 5, "limit": 300} and rep["limits"]["applications_submitted"]["limit"] == 20 and rep["timezone"] == "Asia/Kolkata"
    assert rep["limits"]["applications_submitted"]["used"] == 4                                                         # automated submissions that reached SUBMITTING or later today (b3 is manual)


def test_report_history_is_stored_and_can_be_emailed_once_per_generation(env):
    r1 = env.rt.report.generate("2026-11-13", email=True)
    r2 = env.rt.report.generate("2026-11-13", email=False)
    stored = env.sched.list_reports("2026-11-13")
    assert [x["report_id"] for x in stored] == [r2["report_id"], r1["report_id"]] and r1["email"]["status"] == "sent" and "email" not in r2
    assert env.sched.list_reports("2026-11-12") == [] and len(env.transport.sent) == 1 and b"daily report" in env.transport.sent[0].lower()
    assert "@" not in re.split(r"\r?\n\r?\n", env.transport.sent[0].decode(), maxsplit=1)[1]


def test_report_day_uses_the_local_timezone_boundary(env):
    just_before = datetime(2026, 11, 13, 18, 29, tzinfo=UTC)                                 # 23:59 IST on the 13th
    just_after = datetime(2026, 11, 13, 18, 31, tzinfo=UTC)                                  # 00:01 IST on the 14th
    add_app(env, "a1" * 16, "SUBMITTED", outcome="confirmed", submitted_at=just_before)
    add_app(env, "a2" * 16, "SUBMITTED", outcome="confirmed", submitted_at=just_after)
    assert env.rt.report.build("2026-11-13")["applications"]["submitted"] == 1 and env.rt.report.build("2026-11-14")["applications"]["submitted"] == 1


def test_mail_check_runs_every_30_minutes_inside_the_window_and_reads_only_alert_e_mails(env):
    from scheduler.runtime import MAIL_CHECK_SECONDS
    rt = env.build()
    spec = next(j for j in rt.scheduler.jobs if j.name == "mail_check")
    assert spec.every_seconds == MAIL_CHECK_SECONDS == 30 * 60 and spec.gate is not None
    out = rt.scheduler.run_now("mail_check", "manual")
    assert env.discovery.sources_asked[-1] == ["email_alerts"]                    # alert e-mails only, never a career-page sweep
    assert out is not None


def test_rejection_check_runs_every_30_minutes_and_does_nothing_without_gmail(env):
    from scheduler.runtime import MAIL_CHECK_SECONDS
    rt = env.build()
    spec = next(j for j in rt.scheduler.jobs if j.name == "rejection_check")
    assert spec.every_seconds == MAIL_CHECK_SECONDS and spec.gate is not None
    assert spec.fn("manual")["status"] == "not_set_up"                             # the test credentials file does not exist: nothing is read
