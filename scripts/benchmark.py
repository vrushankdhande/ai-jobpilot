"""Measured (not invented) performance numbers on THIS machine, against a real uvicorn process and a throw-away MongoDB database.

  uv run python scripts/benchmark.py [--jobs 1000] [--apps 300] [--out docs/benchmark_results.json]

Seeds synthetic data into a scratch database (dropped afterwards), starts the API on a free port with isolated storage, measures sequential latency, concurrency,
resume parsing, matching, query plans, background-worker throughput and the API process's memory/CPU, then prints a table and writes JSON.
"""
import argparse
import json
import os
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
import psutil  # noqa: E402
from pymongo import MongoClient  # noqa: E402

from config.settings import get_settings  # noqa: E402
from database.indexes import ensure_indexes  # noqa: E402

UTC = timezone.utc


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def pct(values: list[float], q: float) -> float:
    v = sorted(values)
    return v[min(len(v) - 1, int(round(q * (len(v) - 1))))]


def stats(ms: list[float]) -> dict:
    return {"n": len(ms), "p50_ms": round(pct(ms, 0.5), 1), "p95_ms": round(pct(ms, 0.95), 1), "max_ms": round(max(ms), 1), "mean_ms": round(statistics.mean(ms), 1)}


def seed(db, n_jobs: int, n_apps: int) -> None:
    from ai.schemas.candidate_profile import new_id
    now = datetime.now(UTC)
    sources = ["career_pages", "manual_paste", "linkedin", "indeed"]
    db["jobs"].insert_many([{"job_id": f"{i:032x}", "content_hash": f"bench-{i}", "source": sources[i % 4], "status": "active", "job_title": f"Engineer {i % 50}", "company_name": f"Company {i % 80}",
                             "location": ["Pune", "Bengaluru", "Remote"][i % 3], "job_description": "Python FastAPI MongoDB " * 40, "discovered_at": now - timedelta(hours=i % 700), "created_at": now,
                             "last_updated_at": now, "mandatory_skills": ["Python"], "preferred_skills": ["Docker"], "source_refs": [{"source": sources[i % 4], "url": f"https://x/{i}"}], "dedup": {"status": "unique"}}
                            for i in range(n_jobs)])
    db["job_matches"].insert_many([{"match_id": new_id(), "job_id": f"{i:032x}", "resume_id": "r" * 32, "match_score": 40 + (i * 7) % 60, "classification": "Good Match", "eligibility_status": ["PASS", "FAIL", "UNKNOWN"][i % 3],
                                    "recommendation_code": "RECOMMENDED", "created_at": now - timedelta(hours=i % 500)} for i in range(n_jobs // 2)])
    states = ["READY_FOR_REVIEW", "SUBMITTED", "VERIFIED", "SUBMISSION_FAILED", "MANUAL_HANDOFF_REQUIRED", "VERIFICATION_PENDING", "AWAITING_USER_INPUT"]
    apps, events = [], []
    for i in range(n_apps):
        aid, st = new_id(), states[i % len(states)]
        apps.append({"application_id": aid, "idempotency_key": f"bench-app-{i}", "job_id": f"{i:032x}", "resume_id": "r" * 32, "profile_id": "p" * 32, "profile_version": 1, "source": sources[i % 4], "mode": "manual_handoff",
                     "state": st, "job_title": f"Engineer {i % 50}", "company_name": f"Company {i % 80}", "created_at": now - timedelta(hours=i % 300), "updated_at": now - timedelta(hours=i % 200),
                     "submitted_at": now - timedelta(hours=i % 200) if st in ("SUBMITTED", "VERIFIED", "VERIFICATION_PENDING") else None, "submission": {"outcome": "confirmed" if st in ("SUBMITTED", "VERIFIED") else "not_attempted"},
                     "dismissed_at": None, "verified_at": now if st == "VERIFIED" else None, "mapping": [], "uploads": [], "notes": []})
        events += [{"event_id": new_id(), "application_id": aid, "seq": k, "at": now, "type": "step", "to_state": st, "actor": "system", "detail": {}} for k in range(1, 7)]
    db["applications"].insert_many(apps)
    db["application_events"].insert_many(events)
    db["interviews"].insert_many([{"interview_id": new_id(), "dup_key": f"iv-{i}", "company_name": f"Company {i}", "job_title": "Engineer", "round": "R1", "scheduled_at": now + timedelta(days=i % 20), "status": "scheduled",
                                   "outcome": "pending", "created_at": now, "kind": "video", "duration_minutes": 60, "reminder_minutes_before": 60, "history": []} for i in range(60)])
    db["application_queue"].insert_many([{"task_id": new_id(), "idempotency_key": f"bench-q-{i}", "job_id": f"{i:032x}", "resume_id": "r" * 32, "status": "queued", "priority": 50 + i % 40, "match_score": 80, "eligibility": "PASS",
                                          "job_title": "Engineer", "company_name": "Co", "attempts": 0, "max_attempts": 4, "next_attempt_at": now - timedelta(minutes=1), "created_at": now, "updated_at": now, "history": []}
                                         for i in range(200)])


def timeit(fn, n: int) -> list[float]:
    out = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        out.append((time.perf_counter() - t) * 1000)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=1000)
    ap.add_argument("--apps", type=int, default=300)
    ap.add_argument("--out", default=str(ROOT / "docs" / "benchmark_results.json"))
    a = ap.parse_args()
    s = get_settings()
    client = MongoClient(s.mongodb_uri, serverSelectionTimeoutMS=3000, tz_aware=True)
    dbname = f"ai_jobpilot_bench_{uuid4().hex[:8]}"
    db = client[dbname]
    tmp = Path(tempfile.mkdtemp(prefix="jp_bench_"))
    port = free_port()
    result: dict = {"machine": {"cpu_count": os.cpu_count(), "python": sys.version.split()[0], "platform": sys.platform, "ram_gb": round(psutil.virtual_memory().total / 2**30, 1)},
                    "dataset": {"jobs": a.jobs, "applications": a.apps, "events": a.apps * 6, "queue_tasks": 200, "interviews": 60}, "when": datetime.now(UTC).isoformat()}
    proc = None
    try:
        ensure_indexes(db)
        t0 = time.perf_counter()
        seed(db, a.jobs, a.apps)
        result["seed_seconds"] = round(time.perf_counter() - t0, 2)
        env = {**os.environ, "MONGODB_DATABASE": dbname, "LOG_FILE": str(tmp / "api.log"), "RESUME_STORAGE_DIR": str(tmp / "resumes"), "GENERATED_STORAGE_DIR": str(tmp / "gen"),
               "SCREENSHOTS_DIR": str(tmp / "shots"), "APP_ENV": "development", "SCHEDULER_AUTOSTART": "false"}
        proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"], cwd=str(ROOT), env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = f"http://127.0.0.1:{port}"
        for _ in range(80):
            try:
                if httpx.get(base + "/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.25)
        launcher = psutil.Process(proc.pid)

        class Tree:                                      # the venv python.exe is a launcher: the API is its child process
            def procs(self):
                return [launcher, *launcher.children(recursive=True)]

            def memory_info(self):
                return type("M", (), {"rss": sum(x.memory_info().rss for x in self.procs())})()

            def cpu_times(self):
                ts = [x.cpu_times() for x in self.procs()]
                return type("C", (), {"user": sum(t.user for t in ts), "system": sum(t.system for t in ts)})()
        p = Tree()
        result["memory_rss_mb_idle"] = round(p.memory_info().rss / 2**20, 1)
        http = httpx.Client(base_url=base, timeout=60)

        endpoints = ["/health", "/api/jobs/page?limit=25", "/api/jobs?limit=50", "/api/jobs/page?min_match_score=70&limit=25", "/api/applications/page?limit=25", "/api/applications/page?states=SUBMITTED&states=VERIFIED&q=Company",
                     "/api/dashboard/summary", "/api/analytics/overview", "/api/queue", "/api/interviews?limit=25", "/api/interviews/upcoming", "/api/settings", "/api/scheduler/status", "/api/reports/daily"]
        for e in endpoints:
            http.get(e)                                                                    # warm-up
        result["latency_sequential"] = {e: stats(timeit(lambda e=e: http.get(e).raise_for_status(), 30)) for e in endpoints}

        # ---- concurrency
        def hammer(path: str, threads: int, each: int) -> dict:
            lat, errors = [], []
            lock = threading.Lock()

            def work():
                c = httpx.Client(base_url=base, timeout=60)
                for _ in range(each):
                    t = time.perf_counter()
                    try:
                        r = c.get(path)
                        ok = r.status_code == 200
                    except httpx.HTTPError:
                        ok = False
                    with lock:
                        lat.append((time.perf_counter() - t) * 1000)
                        if not ok:
                            errors.append(1)
            ts = [threading.Thread(target=work) for _ in range(threads)]
            cpu0 = p.cpu_times()
            w0 = time.perf_counter()
            [t.start() for t in ts]
            [t.join() for t in ts]
            wall = time.perf_counter() - w0
            cpu1 = p.cpu_times()
            return {**stats(lat), "threads": threads, "requests": threads * each, "errors": len(errors), "requests_per_second": round(threads * each / wall, 1),
                    "api_cpu_seconds": round((cpu1.user + cpu1.system) - (cpu0.user + cpu0.system), 2), "wall_seconds": round(wall, 2)}
        result["concurrency"] = {path: hammer(path, 20, 10) for path in ("/api/dashboard/summary", "/api/jobs/page?limit=25", "/api/applications/page?limit=25", "/health")}
        result["memory_rss_mb_after_load"] = round(p.memory_info().rss / 2**20, 1)

        # ---- resume parsing (each upload is a NEW file so it is really parsed, not de-duplicated)
        pdf = (ROOT / "tests" / "fixtures" / "sample_resume.pdf").read_bytes()
        docx = (ROOT / "tests" / "fixtures" / "sample_resume.docx").read_bytes()
        parse = {"pdf": [], "docx": []}
        for i in range(8):
            for kind, data, name in (("pdf", pdf + b"\n%bench" + str(i).encode(), "r.pdf"), ("docx", docx, "r.docx")):
                if kind == "docx" and i > 0:
                    continue
                t = time.perf_counter()
                r = http.post("/api/resumes/upload", files={"file": (name, data)})
                parse[kind].append((time.perf_counter() - t) * 1000)
                assert r.status_code in (200, 201), r.text[:100]
        result["resume_parsing_ms"] = {k: stats(v) for k, v in parse.items() if v}

        # ---- matching (30 labelled JDs, deterministic engine)
        sys.path.insert(0, str(ROOT))
        from tests.fixtures.jd_dataset import JDS
        rid = db["resumes"].find_one({}, {"resume_id": 1, "_id": 0})["resume_id"]
        prof = http.get(f"/api/resumes/{rid}/profile").json()
        prof["review_status"] = "reviewed"
        http.put(f"/api/resumes/{rid}/profile", json=prof)
        http.post(f"/api/resumes/{rid}/activate")
        ana, sc = [], []
        for jd in JDS:
            t = time.perf_counter()
            j = http.post("/api/matching/analyze-jd", json={"job_description": jd["text"], "job_title": jd["title"], "company_name": jd["company"], "use_ai": False}).json()
            ana.append((time.perf_counter() - t) * 1000)
            t = time.perf_counter()
            http.post("/api/matching/score", json={"job_id": j["job_id"], "use_ai": False}).raise_for_status()
            sc.append((time.perf_counter() - t) * 1000)
        result["matching_ms"] = {"analyze_jd": stats(ana), "score": stats(sc), "jobs": len(JDS)}
        result["memory_rss_mb_end"] = round(p.memory_info().rss / 2**20, 1)

        # ---- query plans: which key queries use an index?
        def plan(col, q, sort=None):
            c = db[col].find(q)
            if sort:
                c = c.sort(sort)
            ex = c.explain()["queryPlanner"]["winningPlan"]
            stage, found = [], ex
            while found:
                stage.append(found.get("stage"))
                found = found.get("inputStage") or (found.get("queryPlan") if "queryPlan" in found else None)
            return "COLLSCAN" if "COLLSCAN" in stage else "IXSCAN"
        now = datetime.now(UTC)
        result["query_plans"] = {
            "jobs by discovered_at (list)": plan("jobs", {"status": "active"}, [("discovered_at", -1)]), "jobs by source": plan("jobs", {"source": "linkedin"}), "jobs by job_id": plan("jobs", {"job_id": "x"}),
            "applications by state": plan("applications", {"state": "SUBMITTED"}, [("updated_at", -1)]), "applications by job": plan("applications", {"job_id": "x"}), "applications by key": plan("applications", {"idempotency_key": "x"}),
            "events of an application": plan("application_events", {"application_id": "x"}, [("seq", 1)]), "queue claim": plan("application_queue", {"status": "queued", "next_attempt_at": {"$lte": now}}, [("priority", -1)]),
            "interviews upcoming": plan("interviews", {"status": "scheduled", "scheduled_at": {"$gte": now}}, [("scheduled_at", 1)]), "matches by job": plan("job_matches", {"job_id": "x"}),
            "applications created in range": plan("applications", {"created_at": {"$gte": now - timedelta(days=30)}}), "jobs discovered in range": plan("jobs", {"discovered_at": {"$gte": now - timedelta(days=30)}})}
        result["server_cpu_percent_sample"] = psutil.cpu_percent(interval=0.5)
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()

    # ---- background worker throughput (in-process, fake processor so only the queue/worker machinery is measured)
    from database.repositories.applications_repository import ApplicationsRepository
    from database.repositories.queue_repository import QueueRepository
    from database.repositories.scheduler_repository import SchedulerRepository
    from config.automation_config import AutomationLimits
    from scheduler.application_worker import ApplicationWorker, Outcome
    q = QueueRepository(db)
    lim = AutomationLimits(max_jobs_processed_per_day=10_000, max_concurrent_workers=8, application_window=("00:00", "23:59"), retry_backoff_seconds=0)
    worker = ApplicationWorker(q, ApplicationsRepository(db), SchedulerRepository(db), lambda t: Outcome("awaiting_approval", application_id="a" * 32), lambda: lim, "Asia/Kolkata")
    t = time.perf_counter()
    done = 0
    while True:
        out = worker.run_once(max_tasks=8)
        if not out["processed"]:
            break
        done += len(out["processed"])
    el = time.perf_counter() - t
    result["worker"] = {"tasks": done, "seconds": round(el, 2), "tasks_per_second": round(done / el, 1) if el else None}
    # parallel claim: 8 threads, each claim must be unique
    db["application_queue"].update_many({}, {"$set": {"status": "queued", "next_attempt_at": datetime.now(UTC) - timedelta(minutes=1), "lease_expires_at": None}})
    claimed, lk = [], threading.Lock()

    def claimer(n):
        while (t_ := q.claim_next(f"w{n}", 60, datetime.now(UTC), 999)) is not None:
            with lk:
                claimed.append(t_["task_id"])
    ts = [threading.Thread(target=claimer, args=(n,)) for n in range(8)]
    c0 = time.perf_counter()
    [x.start() for x in ts]
    [x.join() for x in ts]
    result["worker"]["parallel_claim"] = {"threads": 8, "tasks": len(claimed), "unique": len(set(claimed)), "seconds": round(time.perf_counter() - c0, 2)}

    # ---- A/B of the YAML cache, same process, interleaved (a fair comparison on a noisy machine): "cache cleared before each request" == the old behaviour
    from fastapi.testclient import TestClient
    from backend.main import app as _app
    from config import yaml_cache
    from database.connection import get_database_manager

    class _M:
        def get_database(self):
            return db
    _app.dependency_overrides[get_database_manager] = lambda: _M()
    tc = TestClient(_app)
    ab = {}
    for path in ("/api/applications/page?limit=25", "/api/jobs/page?limit=25", "/api/settings", "/api/dashboard/summary", "/api/scheduler/status", "/api/queue"):
        tc.get(path)
        off, on = [], []
        for _ in range(40):
            yaml_cache.clear()
            t_ = time.perf_counter()
            tc.get(path)
            off.append((time.perf_counter() - t_) * 1000)
            t_ = time.perf_counter()
            tc.get(path)
            on.append((time.perf_counter() - t_) * 1000)
        ab[path] = {"cache_cleared_p50_ms": round(statistics.median(off), 1), "cache_on_p50_ms": round(statistics.median(on), 1), "speedup": round(statistics.median(off) / max(0.01, statistics.median(on)), 2)}
    _app.dependency_overrides.clear()
    result["yaml_cache_ab"] = ab

    client.drop_database(dbname)
    client.close()
    shutil_rm = __import__("shutil").rmtree
    shutil_rm(tmp, ignore_errors=True)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
