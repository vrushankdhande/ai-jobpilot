"""CRITICAL: duplicate-application protection, proven scenario by scenario (Phase 9 section 6).

Real MongoDB, a real browser and the LOCAL form server (tests/fixtures/form_server.py: it counts every POST it receives). No real application is ever sent.
The invariant under test: for one (candidate, job) the employer's form receives AT MOST ONE submission, and an uncertain outcome is never retried automatically.
"""
import threading
from datetime import datetime, timedelta, timezone

import pytest

from ai.schemas.job_schema import JobInput
from backend.services.application_service import ActionNotAllowed, DuplicateApplication
from database.models.application import idempotency_key
from database.repositories.queue_repository import QueueRepository
from scheduler.application_queue import NotQueueable
from scheduler.runtime import build_runtime
from tests.integration.test_application_flow import (  # noqa: F401  (fixtures + helpers shared with the Phase 6 suite)
    CONSENT, add_job, approve_documents, env, prepare, ready,
)
from tests.integration.test_scheduler_flow import Clock, FakeDiscovery, FakeTransport
from tests.fixtures.jd_dataset import JDS

pytestmark = [pytest.mark.integration, pytest.mark.browser, pytest.mark.safety]
UTC = timezone.utc


def runtime(e, discovery=None):
    """The real Phase 7 runtime (real preparation processor) on the Phase 6 environment, with a controllable clock inside the application window."""
    e.clock = Clock(datetime(2026, 11, 13, 4, 0, tzinfo=UTC))
    return build_runtime(e.db, e.settings, now_fn=e.clock, transport=FakeTransport(), discovery_service=discovery or FakeDiscovery(e.db, e.matching, JDS[:1]))


def queue_task(rt, e, job_id, score=90):
    return rt.queue.enqueue(job_id=job_id, resume_id=e.profile.resume_id, match_score=score, eligibility="PASS", job_title="T", company_name="C")


def approve_and_submit(e, jid):
    a = prepare(e, jid)
    e.service.approve(a["application_id"], acknowledge_warnings=True)
    return a, e.service.submit(a["application_id"])


# ============================================================================================ 1. the same job discovered several times
def test_the_same_job_discovered_repeatedly_is_one_job_one_queue_entry_one_application(env):
    ids = {add_job(env, "/ref", source="linkedin", title="Same Role", company="Same Co") for _ in range(4)}
    assert len(ids) == 1 and env.db["jobs"].count_documents({"job_title": "Same Role"}) == 1                 # content-hash dedup: discovered 4 times, stored once
    jid = ids.pop()
    approve_documents(env, jid)
    first = prepare(env, jid)
    for _ in range(3):
        again = prepare(env, jid)                                                                       # preparing again is idempotent
        assert again["application_id"] == first["application_id"]
    assert env.db["applications"].count_documents({"job_id": jid}) == 1
    rt = runtime(env)
    created = [queue_task(rt, env, jid)[1] for _ in range(3)]
    assert created == [True, False, False] and env.db["application_queue"].count_documents({}) == 1


def test_the_same_vacancy_through_the_discovery_service_import_is_stored_once(env):
    from database.repositories.discovery_runs_repository import DiscoveryRunsRepository
    from database.repositories.jobs_repository import JobsRepository
    from backend.services.discovery_service import DiscoveryService
    from engines.discovery.discovery_engine import DiscoveryEngine
    from integrations.policy import PolicyRegistry
    from integrations.registry import build_adapters
    from config.scoring_config import load_preferences, load_scoring_config
    from engines.matching.skill_extractor import get_catalog
    import httpx
    s = env.settings
    reg = PolicyRegistry.load(s)
    jobs, runs = JobsRepository(env.db), DiscoveryRunsRepository(env.db)
    adapters = build_adapters(s, reg, client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    svc = DiscoveryService(jobs, runs, reg, adapters, DiscoveryEngine(adapters, jobs, runs, reg, get_catalog(s), load_scoring_config(s).seniority.bands), load_preferences(s))
    fields = {"job_title": "ML Engineer", "company_name": "Northwind AI", "location": "Pune", "job_description": JDS[0]["text"]}
    out = [svc.import_job("https://www.linkedin.com/jobs/view/4242", fields) for _ in range(3)]
    assert out[0]["action"] in ("inserted", "new", "created") or out[0]["job"]
    assert env.db["jobs"].count_documents({}) == 1 and len({o["job"]["job_id"] for o in out}) == 1


# ============================================================================================ 2. the same application enters the queue more than once
def test_concurrent_enqueues_of_the_same_job_create_exactly_one_task(env):
    rt = runtime(env)
    results = []
    ts = [threading.Thread(target=lambda: results.append(queue_task(rt, env, "a" * 32)[1])) for _ in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert results.count(True) == 1 and results.count(False) == 11 and env.db["application_queue"].count_documents({}) == 1


def test_a_cancelled_or_failed_task_is_the_same_entry_not_a_second_one(env):
    rt = runtime(env)
    t, _ = queue_task(rt, env, "b" * 32)
    rt.queue.cancel(t["task_id"])
    again, created = queue_task(rt, env, "b" * 32)
    assert created is False and again["task_id"] == t["task_id"] and env.db["application_queue"].count_documents({}) == 1


# ============================================================================================ 3. a worker restarts during processing
def test_a_worker_that_died_after_preparing_does_not_create_a_second_application(env):
    jid = add_job(env, "/apply", source="linkedin", title="Restart Role")                                # manual-only: preparing needs no browser
    approve_documents(env, jid)
    rt = runtime(env)
    task, _ = queue_task(rt, env, jid)
    claimed = rt.queue_repo.claim_next("dead-worker", 60, env.clock(), 5)                               # the worker takes the task ...
    app1 = prepare(env, jid)                                                                           # ... prepares the application ... and dies before reporting
    assert claimed["status"] == "processing" and env.db["applications"].count_documents({}) == 1
    env.clock.advance(seconds=120)                                                                     # lease expires
    out = rt.worker.run_once("new-worker")
    assert out["recovered"] == 1 and out["processed"][0]["status"] == "manual_handoff"
    assert env.db["applications"].count_documents({}) == 1 and rt.queue_repo.get(task["task_id"])["application_id"] == app1["application_id"]
    assert [h["event"] for h in rt.queue_repo.get(task["task_id"])["history"]][:4] == ["queued", "claimed", "recovered", "claimed"]


def test_a_crash_after_the_click_leaves_the_application_unresolved_and_the_restart_never_resubmits(env):
    jid = add_job(env, "/ref", title="Crash After Click")
    approve_documents(env, jid)
    a = prepare(env, jid)
    env.service.approve(a["application_id"], acknowledge_warnings=True)
    rt = runtime(env)
    task, _ = queue_task(rt, env, jid)
    env.db["application_queue"].update_one({"task_id": task["task_id"]}, {"$set": {"status": "processing", "worker_id": "dead", "lease_expires_at": env.clock() - timedelta(minutes=1), "attempts": 1}})
    # the process died while SUBMITTING: the click may or may not have reached the site
    env.db["applications"].update_one({"application_id": a["application_id"]}, {"$set": {"state": "SUBMITTING"}})
    posts_before = len(env.server.posts)
    out = rt.worker.run_once("restarted")
    assert out["recovered"] == 1
    t2 = rt.queue_repo.get(task["task_id"])
    assert t2["status"] == "needs_verification" and len(env.server.posts) == posts_before == 0           # not retried, not resubmitted: a human must look
    with pytest.raises(ActionNotAllowed):
        env.service.submit(a["application_id"])
    with pytest.raises(DuplicateApplication):
        prepare(env, jid)
    assert len(env.server.posts) == 0


# ============================================================================================ 4. the submission succeeds but the response times out
def test_a_submission_whose_response_never_arrives_is_pending_verification_and_is_never_sent_again(env):
    env.settings.browser_timeout_ms = 1500
    jid = add_job(env, "/timeout", title="Timeout Role")
    approve_documents(env, jid)
    a, done = approve_and_submit(env, jid)
    assert done["state"] == "VERIFICATION_PENDING" and done["failure_stage"] == "after_submit"
    assert len(env.server.posts) == 1                                                                   # the site DID receive it exactly once
    for attempt in (lambda: env.service.submit(a["application_id"]), lambda: prepare(env, jid), lambda: env.service.refresh(a["application_id"]),
                    lambda: env.service.approve(a["application_id"])):
        with pytest.raises((ActionNotAllowed, DuplicateApplication)):
            attempt()
    rt = runtime(env)
    with pytest.raises(NotQueueable):
        queue_task(rt, env, jid)                                                                        # it cannot even be queued again
    # a task that somehow exists for it: the worker must flag it for manual verification, not touch the form
    QueueRepository(env.db).enqueue(idempotency_key=idempotency_key(env.profile.resume_id, jid), job_id=jid, resume_id=env.profile.resume_id, priority=9, match_score=90, eligibility="PASS",
                                    job_title="T", company_name="C", source="local_test", max_attempts=4, now=env.clock())
    for _ in range(3):
        rt.worker.run_once("w")
        env.clock.advance(hours=2)
    assert env.db["application_queue"].find_one({})["status"] == "needs_verification" and len(env.server.posts) == 1
    verified = env.service.confirm(a["application_id"], submitted=True, reference="CONF-TIMEOUT-1")      # only a human resolves it
    assert verified["state"] == "VERIFIED" and len(env.server.posts) == 1


# ============================================================================================ 5. two workers attempt the same application
def test_two_workers_cannot_process_the_same_task(env):
    jid = add_job(env, "/apply", source="linkedin", title="Two Workers")
    approve_documents(env, jid)
    rt = runtime(env)
    queue_task(rt, env, jid)
    calls, lock = [], threading.Lock()
    inner = rt.worker.processor

    def counting(task):
        with lock:
            calls.append(task["task_id"])
        return inner(task)
    rt.worker.processor = counting
    ts = [threading.Thread(target=lambda n=n: rt.worker.run_once(f"w{n}")) for n in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(calls) == 1 and env.db["applications"].count_documents({}) == 1


def test_two_simultaneous_submit_requests_send_one_application(env):
    jid = add_job(env, "/ref", title="Double Submit")
    approve_documents(env, jid)
    a = prepare(env, jid)
    env.service.approve(a["application_id"], acknowledge_warnings=True)
    out = []

    def go():
        try:
            out.append(env.service.submit(a["application_id"])["state"])
        except Exception as exc:  # noqa: BLE001
            out.append(type(exc).__name__)
    ts = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(env.server.posts) == 1 and out.count("VERIFIED") == 1 and out.count("ActionNotAllowed") == 3


# ============================================================================================ 6. already submitted / verified
@pytest.mark.parametrize("path", ["/ref", "/noref"])
def test_a_submitted_or_verified_application_can_never_be_repeated(env, path):
    jid = add_job(env, path, title=f"Done {path}")
    approve_documents(env, jid)
    a, done = approve_and_submit(env, jid)
    assert done["state"] in ("VERIFIED", "SUBMITTED") and len(env.server.posts) == 1
    rt = runtime(env)
    for attempt in (lambda: prepare(env, jid), lambda: env.service.submit(a["application_id"]), lambda: env.service.refresh(a["application_id"]), lambda: queue_task(rt, env, jid)):
        with pytest.raises((ActionNotAllowed, DuplicateApplication, NotQueueable)):
            attempt()
    assert env.db["applications"].count_documents({"job_id": jid}) == 1 and len(env.server.posts) == 1
    # even a hand-made queue entry only ever follows the existing application
    QueueRepository(env.db).enqueue(idempotency_key=idempotency_key(env.profile.resume_id, jid), job_id=jid, resume_id=env.profile.resume_id, priority=9, match_score=90, eligibility="PASS",
                                    job_title="T", company_name="C", source="local_test", max_attempts=4, now=env.clock())
    rt.worker.run_once("w")
    assert env.db["application_queue"].find_one({})["status"] == "completed" and len(env.server.posts) == 1


def test_the_idempotency_key_is_unique_in_the_database_itself(env):
    from pymongo.errors import DuplicateKeyError
    jid = add_job(env, "/apply", source="linkedin", title="Index Role")
    approve_documents(env, jid)
    a = prepare(env, jid)
    doc = dict(env.db["applications"].find_one({"application_id": a["application_id"]}, {"_id": 0}))
    doc["application_id"] = "f" * 32
    with pytest.raises(DuplicateKeyError):
        env.db["applications"].insert_one(doc)                                                          # even a buggy writer cannot create a second record for the pair
    t = {"task_id": "e" * 32, "idempotency_key": doc["idempotency_key"], "status": "queued"}
    env.db["application_queue"].insert_one(dict(t))
    with pytest.raises(DuplicateKeyError):
        env.db["application_queue"].insert_one({**t, "task_id": "d" * 32})


def test_a_failed_attempt_that_certainly_sent_nothing_may_be_prepared_again_but_never_twice_active(env):
    jid = add_job(env, "/reject", title="Rejected By Site")
    approve_documents(env, jid)
    a, done = approve_and_submit(env, jid)
    assert done["state"] == "SUBMISSION_FAILED" and done["failure_stage"] == "rejected" and len(env.server.posts) == 1
    again = prepare(env, jid)
    assert again["application_id"] == a["application_id"] and env.db["applications"].count_documents({"job_id": jid}) == 1
