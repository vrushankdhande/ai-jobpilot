"""Integration tests: API + service + repositories on a real (throw-away) MongoDB.

These use the real MongoDB configured in .env, in a randomly named database that
is dropped afterwards. They are skipped when MongoDB is unreachable.
"""
import pytest

pytestmark = pytest.mark.integration


def upload(api, path, name=None, ctype="application/octet-stream"):
    return api.post("/api/resumes/upload", files={"file": (name or path.name, path.read_bytes(), ctype)})


def test_upload_pdf_parse_and_read_profile(api, sample_pdf):
    r = upload(api, sample_pdf)
    assert r.status_code == 201
    body = r.json()
    assert body["duplicate"] is False
    resume = body["resume"]
    assert resume["parsing_status"] == "completed" and resume["is_active"] is True
    assert "stored_filename" not in resume and "path" not in str(resume).lower()

    prof = api.get(f"/api/resumes/{resume['resume_id']}/profile").json()
    assert prof["contact"]["email"] == "jane.sample@example.com"
    assert prof["resume_id"] == resume["resume_id"] and prof["profile_version"] == 1
    assert prof["review_status"] == "pending_review" and prof["confidence_score"] is None
    assert len(prof["work_experience"]) == 2


def test_upload_docx(api, sample_docx):
    r = upload(api, sample_docx)
    assert r.status_code == 201 and r.json()["resume"]["file_type"] == "docx"


def test_status_list_and_details(api, sample_pdf):
    rid = upload(api, sample_pdf).json()["resume"]["resume_id"]
    assert api.get(f"/api/resumes/{rid}/status").json()["parsing_status"] == "completed"
    assert [r["resume_id"] for r in api.get("/api/resumes").json()] == [rid]
    assert api.get(f"/api/resumes/{rid}").json()["original_filename"] == "sample_resume.pdf"


def test_duplicate_upload_is_not_stored_twice(api, sample_pdf, service):
    first = upload(api, sample_pdf).json()["resume"]["resume_id"]
    second = upload(api, sample_pdf)
    assert second.status_code == 200 and second.json()["duplicate"] is True
    assert second.json()["resume"]["resume_id"] == first
    assert len(api.get("/api/resumes").json()) == 1
    assert len(list(service.storage_dir.iterdir())) == 1


def test_profile_update_creates_version_and_marks_reviewed(api, sample_pdf):
    rid = upload(api, sample_pdf).json()["resume"]["resume_id"]
    prof = api.get(f"/api/resumes/{rid}/profile").json()
    prof["contact"]["full_name"] = "Jane Q. Sample"
    prof["technical_skills"]["other_skills"].append("Public Speaking")
    r = api.put(f"/api/resumes/{rid}/profile", json={**prof, "review_status": "reviewed"})
    assert r.status_code == 200
    saved = r.json()
    assert saved["contact"]["full_name"] == "Jane Q. Sample" and saved["profile_version"] == 2
    assert saved["review_status"] == "reviewed"
    assert saved["provenance"]["contact"]["method"] == "manual"
    # edits are flagged (not silently trusted) because they are absent from the source text
    assert any(w["code"] == "unsupported_claim" and w["field"] == "technical_skills.other_skills"
               for w in saved["validation_warnings"])
    # persisted
    again = api.get(f"/api/resumes/{rid}/profile").json()
    assert again["contact"]["full_name"] == "Jane Q. Sample"
    assert api.get(f"/api/resumes/{rid}").json()["review_status"] == "reviewed"
    versions = api.get(f"/api/resumes/{rid}/profile/versions").json()
    assert [v["profile_version"] for v in versions] == [1]
    assert versions[0]["contact"]["full_name"] == "Jane Sample"  # original preserved


def test_profile_update_validation_errors(api, sample_pdf):
    rid = upload(api, sample_pdf).json()["resume"]["resume_id"]
    prof = api.get(f"/api/resumes/{rid}/profile").json()
    prof["contact"]["email"] = "not-an-email"
    assert api.put(f"/api/resumes/{rid}/profile", json=prof).status_code == 422
    assert api.get(f"/api/resumes/{rid}/profile").json()["profile_version"] == 1  # unchanged


def test_reparse_requires_force_and_keeps_corrections_as_version(api, sample_pdf):
    rid = upload(api, sample_pdf).json()["resume"]["resume_id"]
    prof = api.get(f"/api/resumes/{rid}/profile").json()
    prof["contact"]["full_name"] = "Edited Name"
    api.put(f"/api/resumes/{rid}/profile", json=prof)
    assert api.post(f"/api/resumes/{rid}/parse").status_code == 409
    assert api.post(f"/api/resumes/{rid}/parse?force=true").status_code == 200
    now = api.get(f"/api/resumes/{rid}/profile").json()
    assert now["contact"]["full_name"] == "Jane Sample" and now["profile_version"] == 3
    names = [v["contact"]["full_name"] for v in api.get(f"/api/resumes/{rid}/profile/versions").json()]
    assert "Edited Name" in names  # the correction was archived, not lost


def test_multiple_resumes_and_active_selection(api, sample_pdf, sample_docx):
    a = upload(api, sample_pdf).json()["resume"]["resume_id"]
    b = upload(api, sample_docx).json()["resume"]["resume_id"]
    listing = {r["resume_id"]: r for r in api.get("/api/resumes").json()}
    assert listing[a]["is_active"] is True and listing[b]["is_active"] is False  # first upload stays active
    assert api.post(f"/api/resumes/{b}/activate").status_code == 200
    listing = {r["resume_id"]: r for r in api.get("/api/resumes").json()}
    assert listing[a]["is_active"] is False and listing[b]["is_active"] is True
    # both profiles coexist, tied to their own source resume
    assert api.get(f"/api/resumes/{a}/profile").json()["resume_id"] == a
    assert api.get(f"/api/resumes/{b}/profile").json()["resume_id"] == b


def test_delete_removes_everything(api, sample_pdf, service, test_db):
    rid = upload(api, sample_pdf).json()["resume"]["resume_id"]
    api.put(f"/api/resumes/{rid}/profile", json=api.get(f"/api/resumes/{rid}/profile").json())
    assert api.delete(f"/api/resumes/{rid}").status_code == 204
    assert api.get(f"/api/resumes/{rid}").status_code == 404
    assert api.get(f"/api/resumes/{rid}/profile").status_code == 404
    assert api.delete(f"/api/resumes/{rid}").status_code == 404
    for coll in ("resumes", "candidate_profiles", "profile_versions"):
        assert test_db[coll].count_documents({"resume_id": rid}) == 0
    assert list(service.storage_dir.iterdir()) == []
    assert all(not r["is_active"] for r in api.get("/api/resumes").json())


def test_unknown_resume_404(api):
    for path in ("", "/profile", "/status", "/profile/versions"):
        assert api.get(f"/api/resumes/nope{path}").status_code == 404
    assert api.post("/api/resumes/nope/parse").status_code == 404
    assert api.post("/api/resumes/nope/activate").status_code == 404


def test_failed_parse_is_reported_honestly(api, tmp_path):
    import pymupdf
    scan = tmp_path / "scan.pdf"
    doc = pymupdf.open()
    doc.new_page().draw_rect(pymupdf.Rect(50, 50, 300, 300), fill=(0.5, 0.5, 0.5))
    doc.save(str(scan))
    r = upload(api, scan)
    assert r.status_code == 201
    resume = r.json()["resume"]
    assert resume["parsing_status"] == "needs_ocr" and "scanned" in resume["parsing_error"].lower()
    assert api.get(f"/api/resumes/{resume['resume_id']}/profile").status_code == 404  # no fake profile


def test_store_resume_text_setting(make_service, sample_pdf, test_db):
    svc = make_service(store_resume_text=False)
    doc, _ = svc.upload("r.pdf", sample_pdf.read_bytes())
    assert svc.repo.get_extracted_text(doc["resume_id"]) is None
    svc2 = make_service(store_resume_text=True)
    doc2, _ = svc2.upload("r2.docx", sample_pdf.parent.joinpath("sample_resume.docx").read_bytes())
    assert "Jane Sample" in svc2.repo.get_extracted_text(doc2["resume_id"])


def test_repository_behaviour(service, sample_docx, test_db):
    doc, _ = service.upload("cv.docx", sample_docx.read_bytes())
    repo = service.repo
    rid = doc["resume_id"]
    assert repo.get_resume(rid)["sha256"] == repo.find_by_sha256(doc["sha256"])["sha256"]
    assert "extracted_text" not in repo.get_resume(rid)
    assert repo.get_profile(rid).resume_id == rid
    assert repo.update_resume(rid, {"parsing_status": "x"}) and not repo.update_resume("none", {"a": 1})
    indexes = test_db["resumes"].index_information()
    assert "uq_resume_id" in indexes
    from pymongo.errors import DuplicateKeyError
    with pytest.raises(DuplicateKeyError):
        repo.create_resume({"resume_id": rid})
