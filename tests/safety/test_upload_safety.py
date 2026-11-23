"""Safety tests for the upload endpoint (real throw-away MongoDB, skipped if unreachable)."""
import pytest

pytestmark = pytest.mark.integration


def post(api, name, data):
    return api.post("/api/resumes/upload", files={"file": (name, data, "application/pdf")})


def stored_files(service):
    return list(service.storage_dir.iterdir()) if service.storage_dir.exists() else []


def test_wrong_extension_rejected_and_nothing_stored(api, service):
    r = post(api, "malware.exe", b"MZ\x90\x00")
    assert r.status_code == 415 and stored_files(service) == []


def test_pdf_extension_with_exe_content_rejected(api, service):
    assert post(api, "resume.pdf", b"MZ\x90\x00 pretend").status_code == 415
    assert stored_files(service) == []


def test_empty_file_rejected(api):
    assert post(api, "r.pdf", b"").status_code == 400


def test_oversized_file_rejected(make_service, test_db):
    from backend.api.routes.resumes import get_resume_service
    from backend.main import app
    from fastapi.testclient import TestClient

    svc = make_service(max_upload_mb=1)
    app.dependency_overrides[get_resume_service] = lambda: svc
    try:
        r = TestClient(app).post(
            "/api/resumes/upload", files={"file": ("big.pdf", b"%PDF-" + b"0" * (1024 * 1024 + 10), "application/pdf")}
        )
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 413 and stored_files(svc) == []


def test_malformed_pdf_is_stored_but_reported_failed(api):
    r = post(api, "broken.pdf", b"%PDF-1.4 \x00\x01 definitely not a real pdf")
    assert r.status_code == 201
    resume = r.json()["resume"]
    assert resume["parsing_status"] == "failed" and resume["parsing_error"]
    assert api.get(f"/api/resumes/{resume['resume_id']}/profile").status_code == 404


@pytest.mark.parametrize("name", ["../../evil.pdf", "..\\..\\evil.pdf", "/etc/passwd.pdf", "C:\\Windows\\x.pdf"])
def test_path_traversal_filenames_cannot_escape_storage(api, service, sample_pdf, name):
    r = post(api, name, sample_pdf.read_bytes())
    assert r.status_code == 201
    stored = stored_files(service)
    assert len(stored) == 1
    assert stored[0].parent == service.storage_dir
    assert stored[0].name == f"{r.json()['resume']['resume_id']}.pdf"  # server-generated, not user-controlled
    assert "evil" not in stored[0].name and ".." not in r.json()["resume"]["original_filename"]


def test_api_never_exposes_storage_location(api, sample_pdf, service):
    rid = post(api, "cv.pdf", sample_pdf.read_bytes()).json()["resume"]["resume_id"]
    for path in ("", "/status", "/profile"):
        body = api.get(f"/api/resumes/{rid}{path}").text
        assert str(service.storage_dir) not in body and "stored_filename" not in body


def test_missing_and_invalid_profile_fields(api, sample_pdf):
    rid = post(api, "cv.pdf", sample_pdf.read_bytes()).json()["resume"]["resume_id"]
    prof = api.get(f"/api/resumes/{rid}/profile").json()
    bad = {**prof, "work_experience": [{"start_date": "not-a-date"}]}
    assert api.put(f"/api/resumes/{rid}/profile", json=bad).status_code == 422
    bad = {**prof, "education": [{"graduation_year": "abc"}]}
    assert api.put(f"/api/resumes/{rid}/profile", json=bad).status_code == 422
    assert api.put(f"/api/resumes/{rid}/profile", json={"review_status": "bogus"}).status_code == 422
    # a minimal body is valid: missing fields stay missing, nothing is invented
    ok = api.put(f"/api/resumes/{rid}/profile", json={})
    assert ok.status_code == 200 and ok.json()["contact"]["email"] is None
    assert "contact.email" in ok.json()["missing_fields"]


def test_unsupported_ai_style_claims_are_flagged_after_manual_edit(api, sample_pdf):
    rid = post(api, "cv.pdf", sample_pdf.read_bytes()).json()["resume"]["resume_id"]
    prof = api.get(f"/api/resumes/{rid}/profile").json()
    prof["work_experience"].append({"company_name": "Totally Invented Corp", "job_title": "CTO", "start_date": "2010-01"})
    saved = api.put(f"/api/resumes/{rid}/profile", json=prof).json()
    assert any(w["code"] == "unsupported_claim" and "company_name" in w["field"] for w in saved["validation_warnings"])


def test_original_file_never_overwritten(api, service, sample_pdf):
    rid = post(api, "cv.pdf", sample_pdf.read_bytes()).json()["resume"]["resume_id"]
    path = service.storage_dir / f"{rid}.pdf"
    before = path.read_bytes()
    api.post(f"/api/resumes/{rid}/parse?force=true")
    api.put(f"/api/resumes/{rid}/profile", json=api.get(f"/api/resumes/{rid}/profile").json())
    assert path.read_bytes() == before
