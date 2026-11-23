"""Documents on a real throw-away MongoDB: generation, validation, edit-as-new-version, approval, export, download, versions."""
import copy
import hashlib
import io
from pathlib import Path

import pymupdf
import pytest
from docx import Document
from fastapi.testclient import TestClient

from backend.api.routes.documents import get_document_service
from backend.main import app
from backend.services.document_service import DocumentService
from config.scoring_config import Preferences, load_scoring_config
from config.settings import get_settings
from database.repositories.documents_repository import DocumentsRepository
from database.repositories.resumes_repository import ResumesRepository
from engines.matching.skill_extractor import get_catalog
from tests.fixtures.fake_ai import make_client
from tests.fixtures.jd_dataset import JDS

pytestmark = pytest.mark.integration
JD = JDS[0]


@pytest.fixture
def env(make_matching_service, seeded, test_db, tmp_path):
    """(client, service, matching_service, job_id, generated_dir) with a reviewed profile and one stored job."""
    from ai.schemas.job_schema import JobInput
    matching = make_matching_service()
    job_id, _, _ = matching.analyze_jd(JobInput(job_description=JD["text"], job_title=JD["title"], company_name=JD["company"], location="Pune, India"))
    settings = get_settings().model_copy(update={"generated_storage_dir": str(tmp_path / "generated")})

    def build(prefs=None, ai=None):
        return DocumentService(DocumentsRepository(test_db), matching, settings, get_catalog(settings), prefs or Preferences(),
                               load_scoring_config(settings), ai=ai)

    svc = build()
    app.dependency_overrides[get_document_service] = lambda: svc
    yield TestClient(app), svc, matching, job_id, tmp_path / "generated", build
    app.dependency_overrides.clear()


def gen_resume(client, job_id, **kw):
    r = client.post("/api/documents/resume/generate", json={"job_id": job_id, **kw})
    assert r.status_code == 201, r.text
    return r.json()


# ------------------------------------------------------------------------------------ generation
def test_generate_resume_persists_metadata_and_validates(env, test_db, seeded):
    client, svc, matching, job_id, _, _ = env
    d = gen_resume(client, job_id)
    assert d["document_type"] == "resume" and d["version"] == 1 and d["review_status"] == "draft" and d["validation"]["status"] == "passed"
    assert (d["profile_id"], d["resume_id"], d["job_id"], d["profile_version"]) == (seeded.profile_id, seeded.resume_id, job_id, 1)
    assert d["match_id"] and d["generator"] == "deterministic" and d["ai_model"] is None and d["template_version"] and d["generated_at"]
    assert d["content"]["header"]["full_name"] == "Jane Sample" and d["content"]["experience"][0]["company_name"] == "Acme Analytics"
    stored = test_db["documents"].find_one({"document_id": d["document_id"]})
    assert stored and stored["group_id"] == d["group_id"] and stored["validation"]["content_hash"]
    assert d["exports"] == [] and d["profile_stale"] is False
    assert matching.matches.get(d["match_id"]).job_id == job_id           # linked to a real Phase 3 match report


def test_generate_cover_letter_and_screening(env):
    client, _, _, job_id, _, _ = env
    cl = client.post("/api/documents/cover-letter/generate", json={"job_id": job_id}).json()
    assert cl["document_type"] == "cover_letter" and "Northwind AI" in cl["content"]["paragraphs"][0]["text"] and cl["validation"]["status"] != "failed"
    sc = client.post("/api/documents/screening/generate", json={"job_id": job_id, "length": "short"}).json()
    by = {a["question_id"]: a for a in sc["content"]["answers"]}
    assert sc["validation"]["status"] == "failed" and by["notice_period"]["status"] == "needs_input"      # nothing invented
    assert {i["code"] for i in sc["validation"]["issues"] if i["severity"] == "error"} == {"input_required"}
    sc2 = client.post("/api/documents/screening/generate", json={
        "job_id": job_id, "candidate_inputs": {"notice_period": "30 days", "salary_expectation": "INR 25 lakh per year", "relocation": "no",
                                               "work_authorization": "India"}}).json()
    assert sc2["version"] == 2 and sc2["validation"]["status"] in ("passed", "passed_with_warnings")
    assert next(a for a in sc2["content"]["answers"] if a["question_id"] == "notice_period")["answer"] == "My notice period is 30 days."


def test_custom_questions_and_inputs_validation(env):
    client, _, _, job_id, _, _ = env
    r = client.post("/api/documents/screening/generate", json={"job_id": job_id, "questions": [{"text": "Describe a time you failed."}, {"id": "notice_period", "text": "Notice?"}]})
    assert r.status_code == 201 and {a["status"] for a in r.json()["content"]["answers"]} == {"needs_input"}
    for bad in ({"questions": [{"text": "x"}]}, {"questions": [{"id": "Bad Id!", "text": "Question?"}]}, {"length": "huge"},
                {"candidate_inputs": {"favourite_colour": "blue"}}, {"questions": [{"text": "Question?"}] * 16}):
        assert client.post("/api/documents/screening/generate", json={"job_id": job_id, **bad}).status_code == 422


def test_regenerating_creates_a_new_version_and_keeps_the_old_one(env):
    client, _, _, job_id, _, _ = env
    v1 = gen_resume(client, job_id)
    v2 = gen_resume(client, job_id, max_projects=1)
    assert (v1["version"], v2["version"]) == (1, 2) and v1["group_id"] == v2["group_id"] and v2["parent_version_id"] == v1["document_id"]
    assert len(v2["content"]["projects"]) == 1 and len(v1["content"]["projects"]) == 2
    assert client.get(f"/api/documents/{v1['document_id']}").json()["content"] == v1["content"]            # v1 untouched
    versions = client.get(f"/api/documents/{v2['document_id']}/versions").json()
    assert [v["version"] for v in versions] == [1, 2] and all("content" not in v for v in versions)


def test_list_filters_and_latest_only(env):
    client, _, _, job_id, _, _ = env
    gen_resume(client, job_id)
    gen_resume(client, job_id)
    client.post("/api/documents/cover-letter/generate", json={"job_id": job_id})
    latest = client.get("/api/documents").json()
    assert sorted(d["document_type"] for d in latest) == ["cover_letter", "resume"] and {d["version"] for d in latest if d["document_type"] == "resume"} == {2}
    assert len(client.get("/api/documents", params={"latest_only": "false"}).json()) == 3
    assert [d["document_type"] for d in client.get("/api/documents", params={"document_type": "cover_letter"}).json()] == ["cover_letter"]
    assert len(client.get("/api/documents", params={"job_id": job_id, "review_status": "draft"}).json()) == 2
    assert client.get("/api/documents", params={"review_status": "approved"}).json() == []
    assert client.get("/api/documents", params={"document_type": "memo"}).status_code == 422
    assert client.get("/api/documents", params={"limit": 0}).status_code == 422


# ------------------------------------------------------------------------------------ edit / validate / approve
def edit(client, doc, fn):
    content = copy.deepcopy(doc["content"])
    fn(content)
    return client.put(f"/api/documents/{doc['document_id']}", json={"content": content})


def test_edit_creates_a_new_version_and_revalidates(env):
    client, _, _, job_id, _, _ = env
    v1 = gen_resume(client, job_id)
    r = edit(client, v1, lambda c: c.update(summary="Senior ML Engineer focused on retrieval systems and Python."))
    assert r.status_code == 200
    v2 = r.json()
    assert v2["version"] == 2 and v2["edited"] and v2["generator"] == "manual_edit" and v2["review_status"] == "draft" and v2["parent_version_id"] == v1["document_id"]
    assert v2["content"]["summary"].startswith("Senior ML Engineer focused") and client.get(f"/api/documents/{v1['document_id']}").json()["content"]["summary"] != v2["content"]["summary"]
    assert edit(client, v2, lambda c: None).json()["document_id"] == v2["document_id"]                  # no change -> no new version
    assert len(client.get(f"/api/documents/{v1['document_id']}/versions").json()) == 2


def test_fabricated_edit_fails_validation_blocks_approval_and_export(env):
    client, _, _, job_id, _, _ = env
    v1 = gen_resume(client, job_id)
    bad = edit(client, v1, lambda c: (c["skills"][0]["items"].append("Kubernetes"), c["experience"][1].update(start_date="2014-03"),
                                      c["experience"][0]["bullets"].append({"text": "Led a team of 30 engineers and cut costs by 60%.", "source": None}))).json()
    assert bad["validation"]["status"] == "failed"
    codes = {i["code"] for i in bad["validation"]["issues"] if i["severity"] == "error"}
    assert {"unsupported_skill", "date_changed", "unsupported_number"} <= codes
    r = client.post(f"/api/documents/{bad['document_id']}/approve", json={"acknowledge_warnings": True})
    assert r.status_code == 409 and r.json()["detail"]["issues"] and "not supported" in r.json()["detail"]["message"]
    assert client.get(f"/api/documents/{bad['document_id']}").json()["review_status"] == "draft"
    ex = client.post(f"/api/documents/{bad['document_id']}/export", json={"formats": ["pdf"]})
    assert ex.status_code == 409 and "validation errors" in ex.json()["detail"]
    # fix it: the candidate removes the unsupported content -> approvable again
    fixed = edit(client, bad, lambda c: (c["skills"][0]["items"].remove("Kubernetes"), c["experience"][1].update(start_date="2019-06"),
                                         c["experience"][0]["bullets"].pop())).json()
    assert fixed["validation"]["status"] == "passed" and client.post(f"/api/documents/{fixed['document_id']}/approve", json={}).status_code == 200


def test_approval_is_explicit_requires_acknowledging_warnings_and_supersedes(env):
    client, _, _, job_id, _, _ = env
    v1 = gen_resume(client, job_id)
    assert v1["review_status"] == "draft"                                                               # generating never approves
    warn = edit(client, v1, lambda c: c["experience"][0]["bullets"][0].update(text="Built a RAG pipeline with LangChain and MongoDB.")).json()
    assert warn["validation"]["status"] == "passed_with_warnings"
    r = client.post(f"/api/documents/{warn['document_id']}/approve", json={})
    assert r.status_code == 409 and "acknowledge_warnings" in r.json()["detail"]["message"]
    ok = client.post(f"/api/documents/{warn['document_id']}/approve", json={"acknowledge_warnings": True})
    assert ok.status_code == 200 and ok.json()["review_status"] == "approved" and ok.json()["approved_at"]
    newer = gen_resume(client, job_id)                                                                  # regenerate: a draft again
    assert newer["review_status"] == "draft" and newer["version"] == 3
    approved2 = client.post(f"/api/documents/{newer['document_id']}/approve", json={}).json()
    assert approved2["review_status"] == "approved"
    assert client.get(f"/api/documents/{warn['document_id']}").json()["review_status"] == "superseded"   # only one approved version


def test_put_cannot_change_status_or_metadata(env):
    client, _, _, job_id, _, _ = env
    v1 = gen_resume(client, job_id)
    r = client.put(f"/api/documents/{v1['document_id']}", json={"content": v1["content"], "review_status": "approved", "validation": {"status": "passed"}})
    assert r.status_code == 200 and r.json()["review_status"] == "draft" and r.json()["document_id"] == v1["document_id"]
    assert client.get(f"/api/documents/{v1['document_id']}").json()["review_status"] == "draft"


def test_contact_details_cannot_be_edited_in_a_document(env):
    client, _, _, job_id, _, _ = env
    v1 = gen_resume(client, job_id)
    bad = edit(client, v1, lambda c: c["header"].update(email="someone.else@example.com", phone="+1 555 0100")).json()
    assert bad["validation"]["status"] == "failed" and "contact_mismatch" in {i["code"] for i in bad["validation"]["issues"]}


def test_screening_answers_can_be_completed_then_approved(env):
    client, _, _, job_id, _, _ = env
    sc = client.post("/api/documents/screening/generate", json={"job_id": job_id}).json()
    assert client.post(f"/api/documents/{sc['document_id']}/approve", json={}).status_code == 409     # placeholders remain
    def fill(c):
        for a in c["answers"]:
            if a["status"] == "needs_input":
                a.update(answer={"notice_period": "I can join after a 30 day notice period."}.get(a["question_id"], "Yes, I can discuss this in an interview."), status="answered")
        c["candidate_inputs"]["notice_period"] = "30 day"
    done = edit(client, sc, fill).json()
    errs = [(i["field"], i["code"]) for i in done["validation"]["issues"] if i["severity"] == "error"]
    assert not any(c == "input_required" for _, c in errs), errs


def test_validate_endpoint_and_stale_profile_detection(env, test_db, seeded):
    client, _, _, job_id, _, _ = env
    d = gen_resume(client, job_id)
    assert client.post(f"/api/documents/{d['document_id']}/validate").json()["validation"]["status"] == "passed"
    # the candidate later corrects the profile: a skill is removed -> the old resume now claims it, and the document is stale
    repo = ResumesRepository(test_db)
    p = repo.get_profile(seeded.resume_id)
    p.technical_skills.databases.remove("MongoDB")
    p.work_experience[0].technologies_used.remove("MongoDB")
    p.work_experience[0].responsibilities[0] = "Built a retrieval pipeline using LangChain for internal search."
    p.profile_version += 1
    repo.save_profile(p)
    again = client.post(f"/api/documents/{d['document_id']}/validate").json()
    assert again["profile_stale"] is True                       # the document was generated from profile v1, the profile is now v2
    # MongoDB is still in the profile (a project uses it) so the claim is not "unsupported", but the role bullet the resume quotes is
    # no longer in the profile word-for-word: the document must be re-checked by the candidate, not silently trusted
    assert again["validation"]["status"] in ("passed_with_warnings", "failed")
    assert "edited_wording" in {i["code"] for i in again["validation"]["issues"]}
    assert client.post(f"/api/documents/{d['document_id']}/approve", json={}).status_code == 409   # warnings must be acknowledged


# ------------------------------------------------------------------------------------ export / download
def test_export_docx_and_pdf_then_download(env):
    client, _, _, job_id, gen_dir, _ = env
    d = gen_resume(client, job_id)
    r = client.post(f"/api/documents/{d['document_id']}/export", json={"formats": ["docx", "pdf"]})
    assert r.status_code == 200
    out = r.json()
    assert {e["format"] for e in out["exports"]} == {"docx", "pdf"} and all(e["download_name"].startswith("DRAFT_Jane_Sample_Resume_") for e in out["exports"])
    assert all(set(e) == {"format", "download_name", "created_at", "size_bytes", "from_approved", "warnings"} for e in out["exports"])
    assert str(gen_dir) not in str(out) and ".docx\"" not in str(out).replace("download_name", "")   # no stored file ids or server paths
    files = sorted(p.name for p in gen_dir.iterdir())
    assert len(files) == 2 and len({f.split("_")[-1] for f in files}) == 2 and all(f.startswith(d["document_id"][:12]) for f in files)
    pdf = client.get(f"/api/documents/{d['document_id']}/download/pdf")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf" and "attachment" in pdf.headers["content-disposition"]
    assert pdf.headers["x-content-type-options"] == "nosniff" and pdf.content.startswith(b"%PDF-")
    assert "DRAFT" in "".join(p.get_text() for p in pymupdf.open(stream=pdf.content, filetype="pdf"))
    docx = client.get(f"/api/documents/{d['document_id']}/download/docx")
    assert "Acme Analytics" in "\n".join(p.text for p in Document(io.BytesIO(docx.content)).paragraphs)


def test_approved_export_has_no_draft_marker_and_clean_name(env):
    client, _, _, job_id, _, _ = env
    d = gen_resume(client, job_id)
    client.post(f"/api/documents/{d['document_id']}/approve", json={})
    out = client.post(f"/api/documents/{d['document_id']}/export", json={"formats": ["pdf"]}).json()
    assert out["exports"][0]["download_name"] == "Jane_Sample_Resume_Northwind_AI.pdf" and out["exports"][0]["from_approved"] is True
    pdf = client.get(f"/api/documents/{d['document_id']}/download/pdf").content
    assert "DRAFT" not in "".join(p.get_text() for p in pymupdf.open(stream=pdf, filetype="pdf"))


def test_repeated_exports_never_overwrite_each_other(env):
    client, _, _, job_id, gen_dir, _ = env
    d = gen_resume(client, job_id)
    for _ in range(3):
        client.post(f"/api/documents/{d['document_id']}/export", json={"formats": ["docx"]})
    assert len(list(gen_dir.iterdir())) == 3


def test_download_errors(env):
    client, _, _, job_id, _, _ = env
    d = gen_resume(client, job_id)
    assert client.get(f"/api/documents/{d['document_id']}/download/pdf").status_code == 404           # nothing exported yet
    assert client.get(f"/api/documents/{d['document_id']}/download/exe").status_code == 422
    assert client.get("/api/documents/" + "a" * 32 + "/download/pdf").status_code == 404
    assert client.post(f"/api/documents/{d['document_id']}/export", json={"formats": []}).status_code == 422
    assert client.post(f"/api/documents/{d['document_id']}/export", json={"formats": ["rtf"]}).status_code == 422


def test_cover_letter_and_screening_export(env):
    client, _, _, job_id, _, _ = env
    cl = client.post("/api/documents/cover-letter/generate", json={"job_id": job_id}).json()
    out = client.post(f"/api/documents/{cl['document_id']}/export", json={"formats": ["pdf"]}).json()
    assert out["exports"][0]["download_name"] == "DRAFT_Jane_Sample_CoverLetter_Northwind_AI.pdf"
    pdf = client.get(f"/api/documents/{cl['document_id']}/download/pdf").content
    assert "Dear Hiring Manager," in "".join(p.get_text() for p in pymupdf.open(stream=pdf, filetype="pdf"))


# ------------------------------------------------------------------------------------ versions + compare
def test_compare_versions(env):
    client, _, _, job_id, _, _ = env
    v1 = gen_resume(client, job_id)
    v2 = edit(client, v1, lambda c: c.update(summary="A completely different summary of the Python work.")).json()
    diff = client.get(f"/api/documents/{v1['document_id']}/compare/{v2['document_id']}").json()
    assert diff["changed"] and (diff["from_version"], diff["to_version"]) == (1, 2)
    assert any(l.startswith("-") and "Senior ML Engineer with 7+" in l for l in diff["diff"]) and any(l.startswith("+A completely different") for l in diff["diff"])
    swapped = client.get(f"/api/documents/{v2['document_id']}/compare/{v1['document_id']}").json()
    assert (swapped["from_version"], swapped["to_version"]) == (1, 2)                                  # always old -> new
    assert client.get(f"/api/documents/{v1['document_id']}/compare/{v1['document_id']}").json()["changed"] is False
    cl = client.post("/api/documents/cover-letter/generate", json={"job_id": job_id}).json()
    assert client.get(f"/api/documents/{v1['document_id']}/compare/{cl['document_id']}").status_code == 422


# ------------------------------------------------------------------------------------ inputs, profile review, errors
def test_unreviewed_profile_needs_confirmation(env, test_db, seeded):
    client, _, _, job_id, _, _ = env
    repo = ResumesRepository(test_db)
    p = repo.get_profile(seeded.resume_id)
    p.review_status = "pending_review"
    repo.save_profile(p)
    r = client.post("/api/documents/resume/generate", json={"job_id": job_id})
    assert r.status_code == 409 and "not been reviewed" in r.json()["detail"] and test_db["documents"].count_documents({}) == 0
    ok = client.post("/api/documents/resume/generate", json={"job_id": job_id, "allow_unreviewed": True})
    assert ok.status_code == 201


def test_unknown_job_match_and_ids(env):
    client, _, matching, job_id, _, _ = env
    assert client.post("/api/documents/resume/generate", json={"job_id": "f" * 32}).status_code == 404
    assert client.post("/api/documents/resume/generate", json={"job_id": job_id, "match_id": "e" * 32}).status_code == 404
    for bad in ({"job_id": "nope"}, {"job_id": job_id, "match_id": "x"}, {"job_id": job_id, "max_projects": 99}, {"job_id": job_id, "bullets_recent": 0}, {}):
        assert client.post("/api/documents/resume/generate", json=bad).status_code == 422
    assert client.get("/api/documents/not-an-id").status_code == 422 and client.get("/api/documents/" + "a" * 32).status_code == 404
    assert client.post("/api/documents/" + "a" * 32 + "/approve", json={}).status_code == 404
    assert client.put("/api/documents/" + "a" * 32, json={"content": {}}).status_code == 404


def test_match_must_belong_to_the_same_job(env):
    client, _, matching, job_id, _, _ = env
    from ai.schemas.job_schema import JobInput
    other_job, _, _ = matching.analyze_jd(JobInput(job_description=JDS[3]["text"], job_title="Data Scientist"))
    report, _ = matching.score(job_id=other_job)
    r = client.post("/api/documents/resume/generate", json={"job_id": job_id, "match_id": report.match_id})
    assert r.status_code == 422 and "different job" in r.json()["detail"]
    mine, _ = matching.score(job_id=job_id)
    assert client.post("/api/documents/resume/generate", json={"job_id": job_id, "match_id": mine.match_id}).json()["match_id"] == mine.match_id


def test_malformed_edits_are_rejected(env):
    client, _, _, job_id, _, _ = env
    d = gen_resume(client, job_id)
    for bad in ({"experience": "oops"}, {"experience": [{"bullets": [{"text": 5}]}]}, {"education": [{"graduation_year": "abc"}]}):
        assert client.put(f"/api/documents/{d['document_id']}", json={"content": bad}).status_code == 422
    assert client.put(f"/api/documents/{d['document_id']}", json={}).status_code == 422
    assert client.get(f"/api/documents/{d['document_id']}/versions").json().__len__() == 1


# ------------------------------------------------------------------------------------ master resume + cascade
def test_master_resume_and_profile_are_never_modified(env, test_db, seeded, sample_pdf, make_service, tmp_path):
    client, svc, _, job_id, gen_dir, _ = env
    resumes = make_service()
    doc, _ = resumes.upload("master.pdf", sample_pdf.read_bytes())
    stored = resumes.storage_dir / f"{doc['resume_id']}.pdf"
    before_file, before_profile = hashlib.sha256(stored.read_bytes()).hexdigest(), test_db["candidate_profiles"].find_one({"resume_id": seeded.resume_id}, {"_id": 0})
    d = gen_resume(client, job_id)
    edit(client, d, lambda c: c.update(summary="Edited summary about Python."))
    client.post(f"/api/documents/{d['document_id']}/export", json={"formats": ["docx", "pdf"]})
    assert hashlib.sha256(stored.read_bytes()).hexdigest() == before_file
    assert test_db["candidate_profiles"].find_one({"resume_id": seeded.resume_id}, {"_id": 0}) == before_profile
    assert list(resumes.storage_dir.iterdir()) == [stored] and not any(p.parent == resumes.storage_dir for p in gen_dir.iterdir())


def test_deleting_a_resume_removes_its_documents_and_exported_files(env, test_db, seeded, make_service):
    client, svc, _, job_id, gen_dir, _ = env
    d = gen_resume(client, job_id)
    client.post(f"/api/documents/{d['document_id']}/export", json={"formats": ["pdf"]})
    assert len(list(gen_dir.iterdir())) == 1
    resumes = make_service()
    resumes.settings = svc.settings
    resumes.repo.create_resume({"resume_id": seeded.resume_id, "stored_filename": "x.pdf", "file_type": "pdf", "sha256": "z", "uploaded_at": None})
    resumes.delete(seeded.resume_id)
    assert test_db["documents"].count_documents({}) == 0 and list(gen_dir.iterdir()) == []


def test_generation_with_claude_unavailable_or_invalid_falls_back(env):
    client, svc, matching, job_id, _, build = env
    for script in (["junk"], [RuntimeError("down")]):
        app.dependency_overrides[get_document_service] = lambda s=script: build(ai=make_client(s))
        d = client.post("/api/documents/resume/generate", json={"job_id": job_id}).json()
        assert d["generator"] == "deterministic" and d["ai_model"] is None and any("unavailable" in n for n in d["notes"])
    app.dependency_overrides[get_document_service] = lambda: build(ai=make_client(['{"summary": "Expert in Kubernetes with 20 years of experience."}']))
    d = client.post("/api/documents/resume/generate", json={"job_id": job_id}).json()
    assert d["generator"] == "deterministic" and "Kubernetes" not in d["content"]["summary"] and any("rejected" in n for n in d["notes"])
    app.dependency_overrides[get_document_service] = lambda: build(ai=make_client(['{"summary": "Senior ML Engineer with 7+ years of experience in Python and RAG."}']))
    d = client.post("/api/documents/resume/generate", json={"job_id": job_id}).json()
    assert d["generator"] == "claude+deterministic" and d["ai_model"] and d["validation"]["status"] != "failed"
    assert client.get("/api/documents/ai-status").json()["state"] in ("disabled", "no_api_key", "no_consent", "enabled")
