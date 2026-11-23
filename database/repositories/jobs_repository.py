"""Data access for jobs: Phase 3 pasted JDs + cached analyses, and Phase 4 discovered/imported jobs.

One ``jobs`` collection. Phase 3 documents are tolerated by ``JobRecord.from_document``.
"""
import hashlib
import re
from datetime import datetime

from pymongo import ASCENDING, DESCENDING
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from ai.schemas.candidate_profile import new_id, utcnow
from ai.schemas.job_schema import JobAnalysis, JobInput
from database.models.job import JobRecord

JOBS = "jobs"
_NO_ID = {"_id": 0}
_LIST_PROJECTION = {"_id": 0, "analysis": 0, "analysis_key": 0}


def job_content_hash(job: JobInput) -> str:
    """Same job text + title/company/location -> same hash, so a JD is never stored twice."""
    norm = lambda s: re.sub(r"\s+", " ", (s or "").strip().lower())  # noqa: E731
    blob = "|".join([norm(job.job_title), norm(job.company_name), norm(job.location), norm(job.job_description)])
    return hashlib.sha256(blob.encode()).hexdigest()


SORT_FIELDS = {"posted_date": "posted_date", "discovered_at": "discovered_at", "last_updated_at": "last_updated_at",
               "job_title": "job_title", "company_name": "company_name", "experience_min": "experience_min",
               "salary_max": "salary_max"}


class JobsRepository:
    def __init__(self, db: Database):
        self._jobs = db[JOBS]

    # ------------------------------------------------------------------ Phase 3 (pasted JDs)
    def find_by_hash(self, content_hash: str) -> dict | None:
        return self._jobs.find_one({"content_hash": content_hash}, _NO_ID)

    def get_job(self, job_id: str) -> dict | None:
        return self._jobs.find_one({"job_id": job_id}, _NO_ID)

    def urls_for(self, job_ids: list[str]) -> dict[str, str]:
        """job_id -> its posting URL (only jobs that have one). One query for a whole results page."""
        if not job_ids:
            return {}
        cur = self._jobs.find({"job_id": {"$in": list(job_ids)}}, {"_id": 0, "job_id": 1, "job_url": 1})
        return {d["job_id"]: d["job_url"] for d in cur if d.get("job_url")}

    def sources_for(self, job_ids: list[str]) -> dict[str, str]:
        """job_id -> a display name for where the job came from (LinkedIn, Naukri, Google, <provider> career page, ...)."""
        if not job_ids:
            return {}
        known = {"linkedin": "LinkedIn", "naukri": "Naukri", "indeed": "Indeed", "instahyre": "Instahyre", "google": "Google", "glassdoor": "Glassdoor", "monster": "Monster", "foundit": "Foundit"}
        out: dict[str, str] = {}
        cur = self._jobs.find({"job_id": {"$in": list(job_ids)}}, {"_id": 0, "job_id": 1, "source": 1, "job_url": 1, "source_refs": 1})
        for d in cur:
            src = (d.get("source") or "").lower()
            if src in known:
                out[d["job_id"]] = known[src]
                continue
            if src in ("career_pages", "company_career_page"):
                prov = next((r.get("provider") for r in d.get("source_refs") or [] if r.get("provider")), None)
                out[d["job_id"]] = f"Career page ({prov})" if prov else "Career page"
                continue
            host = (re.sub(r"^https?://", "", (d.get("job_url") or "").lower()).split("/")[0]) if d.get("job_url") else ""
            hit = next((v for k, v in known.items() if k in host), None)
            out[d["job_id"]] = hit or (host.removeprefix("www.") if host else "Manual entry")
        return out

    def create_job(self, job: JobInput) -> dict:
        now = utcnow()
        doc = {
            "job_id": new_id(), "content_hash": job_content_hash(job), "source": "manual_paste", "status": "active",
            "job_title": job.job_title, "company_name": job.company_name, "location": job.location, "job_url": job.job_url,
            "employment_type": job.employment_type, "salary": job.salary, "experience_requirement": job.experience_requirement,
            "job_description": job.job_description, "created_at": now, "discovered_at": now, "last_updated_at": now,
            "source_refs": [], "analysis": None, "analysis_key": None, "analyzed_at": None,
        }
        self._jobs.insert_one(dict(doc))
        return doc

    def save_analysis(self, job_id: str, analysis: JobAnalysis, analysis_key: str) -> None:
        self._jobs.update_one({"job_id": job_id}, {"$set": {
            "analysis": analysis.model_dump(mode="json"), "analysis_key": analysis_key, "analyzed_at": utcnow()}})

    def get_cached_analysis(self, job_id: str, analysis_key: str) -> JobAnalysis | None:
        doc = self._jobs.find_one({"job_id": job_id, "analysis_key": analysis_key}, {"_id": 0, "analysis": 1})
        return JobAnalysis.model_validate(doc["analysis"]) if doc and doc.get("analysis") else None

    def delete_job(self, job_id: str) -> bool:
        return self._jobs.delete_one({"job_id": job_id}).deleted_count == 1

    # ------------------------------------------------------------------ Phase 4 (discovery)
    def insert_discovered(self, record: JobRecord) -> tuple[dict, bool]:
        """-> (document, inserted). If an identical job already exists the existing document is returned."""
        doc = record.to_document()
        doc.update({"analysis": None, "analysis_key": None, "analyzed_at": None})
        try:
            self._jobs.insert_one(dict(doc))
            return doc, True
        except DuplicateKeyError:
            existing = self._jobs.find_one({"content_hash": doc["content_hash"]}, _NO_ID)
            return (existing or doc), False

    def candidates_for(self, record: JobRecord, limit: int = 300) -> list[dict]:
        """Existing jobs that could be the same vacancy: same source id, same canonical URL, or same company."""
        queries: list[dict] = []
        for ref in record.source_refs:
            if ref.source_job_id:
                queries.append({"source_refs": {"$elemMatch": {"source": ref.source, "source_job_id": ref.source_job_id}}})
            if ref.canonical_url:
                queries.append({"source_refs.canonical_url": ref.canonical_url})
                queries.append({"canonical_url": ref.canonical_url})
        if record.company_key:
            queries.append({"company_key": record.company_key})
        if not queries:
            return []
        seen: dict[str, dict] = {}
        for doc in self._jobs.find({"$or": queries}, _NO_ID).limit(limit):
            seen[doc["job_id"]] = doc
        return list(seen.values())

    def update_job(self, job_id: str, fields: dict) -> bool:
        return self._jobs.update_one({"job_id": job_id}, {"$set": fields}).matched_count == 1

    def touch_seen(self, job_id: str, ref: dict, now: datetime) -> None:
        """Mark a source reference as seen again (no content change)."""
        self._jobs.update_one(
            {"job_id": job_id, "source_refs": {"$elemMatch": {"source": ref["source"], "source_job_id": ref.get("source_job_id"),
                                                               "canonical_url": ref.get("canonical_url")}}},
            {"$set": {"source_refs.$.last_seen_at": now, "status": "active"}})

    def list_filtered(
        self, *, source: str | None = None, title: str | None = None, company: str | None = None, location: str | None = None,
        work_mode: str | None = None, employment_type: str | None = None, status: str | None = None, skill: str | None = None,
        posted_after: datetime | None = None, posted_before: datetime | None = None, discovered_after: datetime | None = None,
        discovered_before: datetime | None = None, job_ids: list[str] | None = None, needs_review: bool | None = None, min_experience: float | None = None, max_experience: float | None = None,
        sort_by: str = "discovered_at", descending: bool = True, limit: int = 50, skip: int = 0, count_only: bool = False,
    ) -> list[dict] | int:
        q: dict = {}
        if source:
            q["$or"] = [{"source": source}, {"source_refs.source": source}]
        rx = lambda s: {"$regex": re.escape(s), "$options": "i"}  # noqa: E731
        if title:
            q["job_title"] = rx(title)
        if company:
            q["company_name"] = rx(company)
        if location:
            q["location"] = rx(location)
        for key, val in (("work_mode", work_mode), ("employment_type", employment_type), ("status", status)):
            if val:
                q[key] = val
        if skill:
            q["$and"] = [{"$or": [{"mandatory_skills": {"$regex": f"^{re.escape(skill)}$", "$options": "i"}},
                                  {"preferred_skills": {"$regex": f"^{re.escape(skill)}$", "$options": "i"}}]}]
        dates = {}
        if posted_after:
            dates["$gte"] = posted_after
        if posted_before:
            dates["$lte"] = posted_before
        if dates:
            q["posted_date"] = dates
        if discovered_after or discovered_before:
            q["discovered_at"] = {**({"$gte": discovered_after} if discovered_after else {}), **({"$lt": discovered_before} if discovered_before else {})}
        if job_ids is not None:
            q["job_id"] = {"$in": job_ids}
        if needs_review is not None:
            q["dedup.needs_review"] = needs_review
        if min_experience is not None:   # keep jobs whose max requirement reaches the minimum (unknown kept)
            q.setdefault("$and", []).append({"$or": [{"experience_max": None}, {"experience_max": {"$gte": min_experience}},
                                                    {"experience_max": {"$exists": False}}]})
        if max_experience is not None:   # keep jobs asking for no more than the maximum (unknown kept)
            q.setdefault("$and", []).append({"$or": [{"experience_min": None}, {"experience_min": {"$lte": max_experience}},
                                                    {"experience_min": {"$exists": False}}]})
        if count_only:
            return self._jobs.count_documents(q)
        field = SORT_FIELDS.get(sort_by, "discovered_at")
        cur = (self._jobs.find(q, _LIST_PROJECTION).sort([(field, DESCENDING if descending else ASCENDING), ("job_id", ASCENDING)])
               .skip(skip).limit(limit))
        return list(cur)

    def count_filtered(self, **filters) -> int:
        """Same filters as ``list_filtered`` (without paging) - for pagination."""
        for k in ("sort_by", "descending", "limit", "skip"):
            filters.pop(k, None)
        return self.list_filtered(count_only=True, **filters)

    def count_by_source(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for row in self._jobs.aggregate([{"$group": {"_id": {"$ifNull": ["$source", "manual_paste"]}, "n": {"$sum": 1}}}]):
            out[row["_id"]] = row["n"]
        return out

    def count(self, **query) -> int:
        return self._jobs.count_documents(query)
