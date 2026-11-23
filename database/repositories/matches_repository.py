"""Data access for saved match reports (collection ``job_matches``)."""
import re
from datetime import datetime

from pymongo import DESCENDING
from pymongo.database import Database

from ai.schemas.match_schema import MatchReport

MATCHES = "job_matches"
_NO_ID = {"_id": 0}


def min_experience_required(report: MatchReport) -> float | None:
    """The job's stated minimum years of experience (from the eligibility check), or None when the job states none."""
    check = next((c for c in report.eligibility.checks if c.name == "min_experience"), None)
    if check is None or check.status == "NOT_APPLICABLE" or not check.requirement:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", check.requirement)
    return float(m.group(1)) if m else None


class MatchesRepository:
    def __init__(self, db: Database):
        self._matches = db[MATCHES]

    def save(self, report: MatchReport) -> MatchReport:
        doc = report.model_dump(mode="python")
        doc["eligibility_status"] = report.eligibility.status  # flat copy for filtering/indexing
        doc["min_experience_required"] = min_experience_required(report)  # flat copy: the experience filter in the results table
        self._matches.insert_one(doc)
        return report

    @staticmethod
    def _to_report(doc: dict | None) -> MatchReport | None:
        if not doc:
            return None
        doc.pop("eligibility_status", None)
        doc.pop("min_experience_required", None)
        return MatchReport.model_validate(doc)

    def get(self, match_id: str) -> MatchReport | None:
        return self._to_report(self._matches.find_one({"match_id": match_id}, _NO_ID))

    def find_by_input_hash(self, input_hash: str) -> MatchReport | None:
        doc = self._matches.find_one({"input_hash": input_hash}, _NO_ID, sort=[("created_at", DESCENDING)])
        return self._to_report(doc)

    def list_matches(
        self, *, resume_id: str | None = None, min_score: float | None = None, max_score: float | None = None,
        eligibility: str | None = None, classification: str | None = None, recommendation_code: str | None = None, job_title: str | None = None,
        company: str | None = None, date_from: datetime | None = None, date_to: datetime | None = None,
        job_id: str | None = None, job_ids: list[str] | None = None, limit: int = 50, skip: int = 0,
        latest_only: bool = False, max_experience_required: float | None = None, sort: str = "date",
    ) -> list[MatchReport]:
        """``latest_only``: one row per job (its newest match, filters applied to THAT match). ``max_experience_required``: jobs asking for at most
        this many years (jobs that state no minimum are included). ``sort``: "date" (newest first) or "score" (highest first)."""
        q: dict = {}
        base: dict = {}
        if resume_id:
            base["resume_id"] = resume_id
        if job_id:
            base["job_id"] = job_id
        elif job_ids is not None:                 # restrict to these jobs (an empty list means no job qualifies)
            base["job_id"] = {"$in": list(job_ids)}
        q.update(base)
        if min_score is not None or max_score is not None:
            q["match_score"] = {**({"$gte": min_score} if min_score is not None else {}),
                                **({"$lte": max_score} if max_score is not None else {})}
        if eligibility:
            q["eligibility_status"] = eligibility
        if classification:
            q["classification"] = classification
        if recommendation_code:
            q["recommendation_code"] = recommendation_code
        if job_title:
            q["job_title"] = {"$regex": re.escape(job_title), "$options": "i"}
        if company:
            q["company_name"] = {"$regex": re.escape(company), "$options": "i"}
        if date_from or date_to:
            q["created_at"] = {**({"$gte": date_from} if date_from else {}), **({"$lte": date_to} if date_to else {})}
        if max_experience_required is not None:
            q["$or"] = [{"min_experience_required": None}, {"min_experience_required": {"$lte": max_experience_required}}]
        order = [("match_score", DESCENDING), ("created_at", DESCENDING)] if sort == "score" else [("created_at", DESCENDING)]
        if latest_only:
            rest = {k: v for k, v in q.items() if k not in base}
            pipeline: list[dict] = [{"$match": base}, {"$sort": {"created_at": -1}}, {"$group": {"_id": "$job_id", "doc": {"$first": "$$ROOT"}}},
                                    {"$replaceRoot": {"newRoot": "$doc"}}]
            if rest:
                pipeline.append({"$match": rest})
            pipeline += [{"$sort": dict(order)}, {"$skip": skip}, {"$limit": limit}, {"$project": {"_id": 0}}]
            return [self._to_report(d) for d in self._matches.aggregate(pipeline, allowDiskUse=True)]
        cur = self._matches.find(q, _NO_ID).sort(order).skip(skip).limit(limit)
        return [self._to_report(d) for d in cur]

    def history(self, resume_id: str, job_id: str) -> list[MatchReport]:
        return self.list_matches(resume_id=resume_id, job_id=job_id, limit=200)

    def delete_for_resume(self, resume_id: str) -> int:
        return self._matches.delete_many({"resume_id": resume_id}).deleted_count
