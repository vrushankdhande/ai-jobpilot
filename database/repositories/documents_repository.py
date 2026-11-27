"""Data access for generated documents (collection ``documents``). Every version is its own document."""
from pymongo import ASCENDING, DESCENDING
from pymongo.database import Database

from ai.schemas.document_schema import GeneratedDocument

DOCUMENTS = "documents"
_NO_ID = {"_id": 0}
_LIST_PROJECTION = {"_id": 0, "content": 0}


class DocumentsRepository:
    def __init__(self, db: Database):
        self._docs = db[DOCUMENTS]

    def insert(self, doc: GeneratedDocument) -> dict:
        data = doc.model_dump(mode="python")
        self._docs.insert_one(dict(data))
        return data

    def get(self, document_id: str) -> dict | None:
        return self._docs.find_one({"document_id": document_id}, _NO_ID)

    def update(self, document_id: str, fields: dict) -> bool:
        return self._docs.update_one({"document_id": document_id}, {"$set": fields}).matched_count == 1

    def push_export(self, document_id: str, export: dict) -> None:
        self._docs.update_one({"document_id": document_id}, {"$push": {"exports": export}})

    def latest_version(self, group_id: str) -> dict | None:
        return self._docs.find_one({"group_id": group_id}, _NO_ID, sort=[("version", DESCENDING)])

    def group(self, group_id: str, include_content: bool = False) -> list[dict]:
        cur = self._docs.find({"group_id": group_id}, _NO_ID if include_content else _LIST_PROJECTION).sort("version", ASCENDING)
        return list(cur)

    def supersede_others(self, group_id: str, keep_id: str) -> None:
        self._docs.update_many({"group_id": group_id, "document_id": {"$ne": keep_id}, "review_status": "approved"},
                               {"$set": {"review_status": "superseded"}})

    def find_all(self, *, document_type: str | None = None, job_id: str | None = None, resume_id: str | None = None,
             review_status: str | None = None, latest_only: bool = True, limit: int = 50, skip: int = 0) -> list[dict]:
        match: dict = {}
        for key, val in (("document_type", document_type), ("job_id", job_id), ("resume_id", resume_id), ("review_status", review_status)):
            if val:
                match[key] = val
        if not latest_only:
            return list(self._docs.find(match, _LIST_PROJECTION).sort([("generated_at", DESCENDING), ("document_id", ASCENDING)]).skip(skip).limit(limit))
        pipeline = [{"$match": match}, {"$sort": {"version": -1}}, {"$group": {"_id": "$group_id", "doc": {"$first": "$$ROOT"}}},
                    {"$replaceRoot": {"newRoot": "$doc"}}, {"$project": {"_id": 0, "content": 0}},
                    {"$sort": {"generated_at": -1}}, {"$skip": skip}, {"$limit": limit}]
        return list(self._docs.aggregate(pipeline))

    def pop_for_resume(self, resume_id: str) -> list[dict]:
        """Remove and return every document of a resume (so the caller can delete the exported files)."""
        docs = list(self._docs.find({"resume_id": resume_id}, {"_id": 0, "document_id": 1, "exports": 1}))
        self._docs.delete_many({"resume_id": resume_id})
        return docs
