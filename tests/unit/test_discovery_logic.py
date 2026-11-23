"""ATS detection, normalisation, URL canonicalisation and duplicate detection (pure logic, no network)."""
from datetime import datetime, timedelta, timezone

import pytest

from database.models.job import JobRecord
from engines.discovery.deduplication import (
    DedupConfig, canonical_url, company_key, decide, description_similarity, location_keys, title_key,
)
from engines.discovery.job_normalizer import InvalidJob, clean_title, normalize, parse_date_any
from integrations.base_adapter import RawJob
from integrations.career_pages.ats_detector import detect
from tests.fixtures.discovery_data import DESC_ML

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)


# ============================================================ ATS detection
@pytest.mark.parametrize("url,provider,token", [
    ("https://boards.greenhouse.io/acmeanalytics/jobs/101", "greenhouse", "acmeanalytics"),
    ("https://job-boards.greenhouse.io/acme", "greenhouse", "acme"),
    ("https://boards-api.greenhouse.io/v1/boards/acme/jobs", "greenhouse", "acme"),
    ("https://jobs.lever.co/betalabs/11111111-2222-3333-4444-555555555555", "lever", "betalabs"),
    ("https://api.lever.co/v0/postings/betalabs?mode=json", "lever", "betalabs"),
    ("https://bigco.wd5.myworkdayjobs.com/en-US/External", "workday", "bigco"),
])
def test_ats_detection_from_url(url, provider, token):
    d = detect(url)
    assert (d.provider, d.board_token) == (provider, token) and d.confidence >= 0.9 and d.supported


def test_ats_detection_from_html_is_weaker_and_separate_from_extraction():
    d = detect("https://careers.acme.example/jobs", '<script src="https://boards.greenhouse.io/embed/job_board/js?for=acmeco"></script>')
    assert (d.provider, d.board_token) == ("greenhouse", "acmeco") and 0.6 <= d.confidence < 0.95
    assert detect("https://x.example", '<a href="https://jobs.lever.co/zeta">x</a>').provider == "lever"
    assert detect("https://x.example", '<script type="application/ld+json">{"@type": "JobPosting"}</script>').provider == "generic"
    assert detect("https://x.example", "<html>myworkdayjobs.com/foo</html>").provider == "workday"


@pytest.mark.parametrize("url,html", [("https://careers.example.org/jobs", "<html><body>plain page</body></html>"), (None, None),
                                      ("https://example.org/?gh_jid=123", None), ("not a url", "")])
def test_unidentified_providers_are_unsupported(url, html):
    d = detect(url, html)
    assert d.provider == "unsupported" and not d.supported


def test_unsafe_tokens_are_never_returned():
    assert detect("https://boards.greenhouse.io/ac%2e%2e/jobs/1").board_token is None
    assert detect("https://jobs.lever.co/a b/1").board_token is None


# ============================================================ URL canonicalisation
@pytest.mark.parametrize("raw,expected", [
    ("https://boards.greenhouse.io/acme/jobs/101?gh_src=abc&utm_source=li&utm_medium=x#apply", "https://boards.greenhouse.io/acme/jobs/101"),
    ("https://job-boards.greenhouse.io/acme/jobs/101", "https://boards.greenhouse.io/acme/jobs/101"),
    ("HTTP://WWW.Example.COM:443/Careers//Job/1/?utm_campaign=z&b=2&a=1&fbclid=q", "https://example.com/Careers/Job/1?a=1&b=2"),
    ("https://www.linkedin.com/jobs/view/senior-ml-engineer-at-acme-3912345678/?trackingId=x&refId=y", "https://linkedin.com/jobs/view/3912345678"),
    ("https://in.linkedin.com/jobs/view/3912345678", "https://linkedin.com/jobs/view/3912345678"),
    ("https://www.linkedin.com/jobs/search/?currentJobId=3912345678&keywords=ml", "https://linkedin.com/jobs/view/3912345678"),
    ("https://in.indeed.com/viewjob?jk=a1b2c3d4e5f60718&from=serp&vjs=3", "https://in.indeed.com/viewjob?jk=a1b2c3d4e5f60718"),
    ("https://jobs.lever.co/betalabs/11111111-2222-3333-4444-555555555555/apply?lever-source=x", "https://jobs.lever.co/betalabs/11111111-2222-3333-4444-555555555555"),
    ("https://acme.wd5.myworkdayjobs.com/en-US/Ext/job/Pune/ML-Engineer_R123?source=x", "https://acme.wd5.myworkdayjobs.com/en-US/Ext/job/Pune/ML-Engineer_R123"),
    ("https://careers.acme.example/job?id=42&utm_source=a", "https://careers.acme.example/job?id=42"),
    ("https://careers.acme.example/job?gh_jid=77&gh_src=t", "https://careers.acme.example/job?gh_jid=77"),
])
def test_canonical_url(raw, expected):
    assert canonical_url(raw) == expected


@pytest.mark.parametrize("bad", [None, "", "javascript:alert(1)", "ftp://x/y", "/relative/path", "https://"])
def test_canonical_url_rejects_non_http(bad):
    assert canonical_url(bad) is None


def test_canonical_url_keeps_identifying_parameters():
    a = canonical_url("https://careers.example.com/jobs?jobId=1")
    b = canonical_url("https://careers.example.com/jobs?jobId=2")
    assert a != b  # unknown/identifying params are never dropped


# ============================================================ keys + similarity
@pytest.mark.parametrize("a,b", [("Sr. ML Engineer (m/f/d)", "Senior Machine Learning Engineer"), ("Data Scientist - R12345", "Data Scientist"),
                                 ("  Software   Engineer [Remote] ", "software engineer")])
def test_title_keys_match_equivalent_titles(a, b):
    assert title_key(a) == title_key(b)


@pytest.mark.parametrize("a,b", [("Senior Data Scientist", "Junior Data Scientist"), ("Data Scientist", "Data Engineer")])
def test_title_keys_keep_different_jobs_apart(a, b):
    assert title_key(a) != title_key(b)


def test_company_and_location_keys():
    assert company_key("The Acme Analytics Pvt. Ltd.") == company_key("ACME ANALYTICS, Inc") == "acme analytics"
    assert company_key("Acme") != company_key("Acme Labs")
    assert location_keys(["Bangalore, India", "Bengaluru", "Pune (Hybrid)", "Bombay"]) == ["bengaluru", "pune", "mumbai"]


def test_description_similarity():
    assert description_similarity(DESC_ML, DESC_ML) == 1.0
    assert description_similarity(DESC_ML, "Completely different words about cooking pasta in a restaurant kitchen every single day") < 0.1
    assert description_similarity("too short", DESC_ML) is None


# ============================================================ normalisation
def raw(**kw):
    base = dict(source="company_career_page", provider="greenhouse", source_job_id="101", url="https://boards.greenhouse.io/acme/jobs/101?gh_src=x",
                title="Machine Learning Engineer (m/f/d)", company="Acme Analytics Pvt. Ltd.", location_text=["Pune, India"],
                description="Requirements:\n- 3+ years of experience in machine learning.\n- Strong Python and SQL skills.\n- Experience with PyTorch or TensorFlow.\n"
                            "Nice to have:\n- Docker.\nLocation: Pune, India (Hybrid). Salary: INR 25-35 LPA. Full-time.",
                posted_raw="2026-11-01T08:30:00-05:00", original={"board_token": "acme"})
    base.update(kw)
    return RawJob(**base)


def test_normalize_full_record(catalog):
    r = normalize(raw(), catalog, now=NOW)
    assert r.job_title == "Machine Learning Engineer" and r.company_name == "Acme Analytics Pvt. Ltd." and r.company_key == "acme analytics"
    assert r.location == ["Pune, India"] and r.work_mode == "hybrid" and r.employment_type == "full-time"
    assert {"Python", "SQL", "Machine Learning"} <= set(r.mandatory_skills) and r.preferred_skills == ["Docker"]
    assert r.experience_min == 3 and (r.salary_min, r.salary_max, r.currency) == (2500000, 3500000, "INR")
    assert r.job_url == "https://boards.greenhouse.io/acme/jobs/101" and r.canonical_url == r.job_url
    assert r.posted_date == datetime(2026, 11, 1, 13, 30, tzinfo=timezone.utc) and r.status == "active"
    assert r.discovered_at == r.last_updated_at == NOW and r.content_hash and r.normalizer_version
    ref = r.source_refs[0]
    assert ref.url.endswith("gh_src=x") and ref.original["job_title"] == "Machine Learning Engineer (m/f/d)"  # original preserved
    assert ref.original["board_token"] == "acme" and ref.content_hash == r.content_hash
    assert JobRecord.model_validate(r.to_document()).job_id == r.job_id  # round-trips through Mongo shape


def test_unknown_values_stay_unknown(catalog):
    r = normalize(RawJob(source="manual", title="Analyst", url="https://example.org/1"), catalog, now=NOW)
    assert (r.company_name, r.job_description, r.work_mode, r.employment_type, r.salary_min, r.posted_date, r.experience_min) == (None,) * 7
    assert r.location == [] and r.mandatory_skills == [] and {"company unknown", "description unknown"} <= set(r.warnings)


def test_structured_fields_win_and_nothing_is_guessed(catalog):
    r = normalize(raw(salary_min=3000000, salary_max=4000000, salary_currency="inr", employment_text="PART_TIME", work_mode_text="remote",
                      description="We are hiring.", location_text=["Remote - India"]), catalog, now=NOW)
    assert (r.salary_min, r.salary_max, r.currency, r.employment_type, r.work_mode, r.location) == (3000000, 4000000, "INR", "part-time", "remote", ["India"])
    assert "description too short to analyse" in r.warnings and r.mandatory_skills == []


@pytest.mark.parametrize("title,expected", [("Data Scientist (m/f/d)", "Data Scientist"), ("Engineer [f/m/x]", "Engineer"),
                                            ("Software Engineer - R12345", "Software Engineer"), ("  ML   Engineer ", "ML Engineer"), ("&amp; Co Analyst", "& Co Analyst")])
def test_clean_title(title, expected):
    assert clean_title(title) == expected


@pytest.mark.parametrize("value,expected", [
    ("2026-11-01T08:30:00Z", datetime(2026, 11, 1, 8, 30, tzinfo=timezone.utc)), ("2026-11-05", datetime(2026, 11, 5, tzinfo=timezone.utc)),
    (1762070400000, datetime(2025, 11, 2, 8, 0, tzinfo=timezone.utc)), (1762070400, datetime(2025, 11, 2, 8, 0, tzinfo=timezone.utc)),
    ("Nov 3, 2026", datetime(2026, 11, 3, tzinfo=timezone.utc)), ("3 days ago", NOW - timedelta(days=3)), ("2 weeks ago", NOW - timedelta(weeks=2)),
    ("today", NOW), ("Yesterday", NOW - timedelta(days=1)), ("garbage", None), ("", None), (None, None), (True, None),
])
def test_parse_date_any(value, expected):
    assert parse_date_any(value, NOW) == expected


def test_bad_values_become_warnings_not_data(catalog):
    r = normalize(raw(posted_raw="sometime soon", url="javascript:alert(1)", salary_min=5, salary_max=1), catalog, now=NOW)
    assert r.posted_date is None and r.job_url is None and r.salary_min is None
    assert any("posted date" in w for w in r.warnings) and any("URL" in w for w in r.warnings)
    future = normalize(raw(posted_raw="2030-01-01"), catalog, now=NOW)
    assert future.posted_date is None and any("future" in w for w in future.warnings)


def test_unusable_record_is_rejected(catalog):
    with pytest.raises(InvalidJob):
        normalize(RawJob(source="x"), catalog, now=NOW)


def test_job_model_validation():
    with pytest.raises(Exception):
        JobRecord(source="x", salary_min=10, salary_max=5)
    with pytest.raises(Exception):
        JobRecord(source="x", job_url="http://localhost/x")
    with pytest.raises(Exception):
        JobRecord(source="x", currency="rupees")
    assert JobRecord(source="x").status == "active"


# ============================================================ duplicate detection
def rec(catalog, **kw):
    return normalize(raw(**kw), catalog, now=NOW)


def doc(record: JobRecord) -> dict:
    return record.to_document()


def test_same_source_id_is_the_same_vacancy(catalog):
    old, new = rec(catalog), rec(catalog)
    d = decide(new, [doc(old)])
    assert (d.action, d.level, d.confidence) == ("unchanged", "exact_source_id", 1.0)
    changed = rec(catalog, description=raw().description + "\n- Kubernetes experience.")
    assert decide(changed, [doc(old)]).action == "update"


def test_same_id_at_a_different_company_is_not_the_same_job(catalog):
    a = rec(catalog)
    b = rec(catalog, company="Totally Different Corp", url="https://boards.greenhouse.io/other/jobs/101", original={"board_token": "other"})
    assert decide(b, [doc(a)]).action == "insert"  # Greenhouse ids can collide across boards


def test_canonical_url_match(catalog):
    a = rec(catalog)
    b = rec(catalog, source_job_id=None, url="https://job-boards.greenhouse.io/acme/jobs/101?utm_source=x")
    d = decide(b, [doc(a)])
    assert d.level == "canonical_url" and d.action in ("unchanged", "update")


def test_cross_platform_duplicate_is_merged(catalog):
    gh = rec(catalog)
    def on_linkedin(title):
        return normalize(RawJob(source="linkedin", source_job_id="3912345678", url="https://www.linkedin.com/jobs/view/3912345678?trackingId=x",
                                title=title, company="Acme Analytics", location_text=["Pune, Maharashtra, India"],
                                description=raw().description + "\nApply now on LinkedIn."), catalog, now=NOW)

    d = decide(on_linkedin("Machine Learning Engineer"), [doc(gh)])
    assert d.action == "merge" and d.level == "cross_platform" and d.match_job_id == gh.job_id and d.confidence > 0.9
    # "Sr." vs no seniority may be a different level: never auto-merged
    assert decide(on_linkedin("Sr. Machine Learning Engineer"), [doc(gh)]).action != "merge"


def test_different_jobs_at_the_same_company_are_not_merged(catalog):
    base = rec(catalog)
    other_city = rec(catalog, source_job_id="202", url="https://boards.greenhouse.io/acme/jobs/202", location_text=["Berlin, Germany"])
    other_team = rec(catalog, source_job_id="303", url="https://boards.greenhouse.io/acme/jobs/303",
                     description="Completely different role working on mobile apps for payments with Kotlin and Swift. Requirements: 2+ years Android.")
    other_req = rec(catalog, source_job_id="404", url="https://boards.greenhouse.io/acme/jobs/404", description=raw().description + "\nRequisition ID: R-9999")
    base_req = rec(catalog, description=raw().description + "\nRequisition ID: R-1111")
    assert decide(other_city, [doc(base)]).action == "insert"          # same title, other city
    assert decide(other_team, [doc(base)]).action == "insert"          # same title, unrelated description
    assert decide(other_req, [doc(base_req)]).action == "insert"       # different requisition ids


def test_uncertain_matches_are_flagged_never_merged(catalog):
    base = rec(catalog)
    half = " ".join(raw().description.split()[:25]) + " plus a long paragraph about our offices, perks, culture, hackathons and annual retreats in the hills."
    sim_other = rec(catalog, source_job_id="505", url="https://boards.greenhouse.io/acme/jobs/505", description=half)
    d = decide(sim_other, [doc(base)])
    assert d.action in ("flag", "insert") and d.action != "merge"
    fuzzy = rec(catalog, source_job_id="606", url="https://boards.greenhouse.io/acme/jobs/606", title="Machine Learning Software Engineer")
    f = decide(fuzzy, [doc(base)])
    assert f.action in ("flag", "insert") and f.action != "merge"  # similar (not same) title is never auto-merged
    no_desc = rec(catalog, source_job_id="707", url="https://boards.greenhouse.io/acme/jobs/707", description=None)
    n = decide(no_desc, [doc(base)])
    assert n.action == "flag" and n.confidence < 0.7  # same title+place but nothing to compare -> review


def test_unknown_location_needs_stronger_evidence(catalog):
    base = rec(catalog)
    no_loc = rec(catalog, source_job_id="808", url="https://boards.greenhouse.io/acme/jobs/808", location_text=[],
                 description=raw().description + " Extra sentence about how we onboard.")
    assert decide(no_loc, [doc(base)], DedupConfig(merge_similarity=0.5, merge_similarity_unknown_location=0.999)).action != "merge"


def test_decision_priority_prefers_exact_over_fuzzy(catalog):
    a, b = rec(catalog), rec(catalog, source_job_id="999", url="https://boards.greenhouse.io/acme/jobs/999")
    d = decide(rec(catalog), [doc(b), doc(a)])
    assert d.match_job_id == a.job_id and d.level == "exact_source_id"


def test_no_candidates_means_insert(catalog):
    assert decide(rec(catalog), []).action == "insert"
