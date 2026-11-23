"""Policy registry, adapter interface, permission enforcement, HTTP guard, rate limiting, providers (all mocked)."""
import inspect

import httpx
import pytest

from config.settings import Settings
from integrations.base_adapter import (
    AdapterError,
    JobAdapter,
    ManualOnlyAdapter,
    PolicyHttp,
    RateLimiter,
    RateLimitExceeded,
    RawJob,
    SearchQuery,
    SourceBlocked,
    is_private_host,
    public_http_url,
)
from integrations.career_pages.adapter import CareerPagesAdapter, CompanySource
from integrations.career_pages.providers.generic_form import GenericCareerPageAdapter, extract_job_postings
from integrations.career_pages.providers.greenhouse import GreenhouseAdapter
from integrations.career_pages.providers.lever import LeverAdapter
from integrations.career_pages.providers.workday import WorkdayAdapter
from integrations.indeed.adapter import IndeedAdapter
from integrations.linkedin.adapter import LinkedInAdapter
from integrations.manual_adapter import ManualImportAdapter
from integrations.naukri.adapter import NaukriAdapter
from integrations.policy import Policy, PolicyRegistry, PolicyViolation, SourceStatus
from integrations.registry import build_adapters
from tests.fixtures.discovery_data import (
    GREENHOUSE_LIST, JSONLD_PAGE, LEVER_LIST, LISTING_PAGE, Recorder, html_response, json_response,
)

pytestmark = pytest.mark.usefixtures("no_network")
UA = "AI-JobPilot-test"


def policy(status="ENABLED", hosts=("boards-api.greenhouse.io",), rpm=600, budget=100, **kw):
    return Policy(name="t", status=SourceStatus(status), allowed_hosts=list(hosts), requests_per_minute=rpm,
                  max_requests_per_run=budget, manual_import=kw.pop("manual_import", True), **kw)


def http_for(rec: Recorder, pol: Policy, extra=(), sleep=lambda s: None, clock=None):
    limiter = RateLimiter(pol.requests_per_minute, pol.max_requests_per_run, clock=clock or (lambda: 0.0), sleep=sleep)
    return PolicyHttp(pol, rec.client(), limiter, UA, extra_hosts=extra)


def company(**kw):
    return CompanySource(name=kw.pop("name", "Acme Analytics"), **kw)


# ============================================================ policy registry (fail closed)
@pytest.fixture(scope="module")
def registry():
    return PolicyRegistry.load(Settings())


def test_shipped_policy_statuses(registry):
    assert registry.get("linkedin").status == SourceStatus.MANUAL_ONLY
    assert registry.get("indeed").status == SourceStatus.MANUAL_ONLY
    assert registry.get("naukri").status == SourceStatus.PENDING_POLICY_REVIEW
    assert registry.get("career_pages").status == SourceStatus.ENABLED
    assert registry.get("career_pages", "greenhouse").status == SourceStatus.ENABLED
    assert registry.get("career_pages", "lever").status == SourceStatus.ENABLED
    assert registry.get("career_pages", "workday").status == SourceStatus.PENDING_POLICY_REVIEW
    for s in ("linkedin", "indeed", "naukri"):
        assert registry.get(s).automated_allowed is False and registry.get(s).manual_allowed is True
    assert all(registry.get(s).needs_human_confirmation for s in ("linkedin", "indeed", "naukri", "career_pages"))
    assert registry.get("linkedin").reviewed_on and registry.get("linkedin").basis


def test_unknown_source_is_disabled():
    reg = PolicyRegistry({"career_pages": {"discovery_status": "ENABLED"}})
    p = reg.get("some_new_site")
    assert p.status == SourceStatus.DISABLED and not p.automated_allowed and not p.manual_allowed
    assert "not listed" in p.deny_reason()


@pytest.mark.parametrize("value", ["yes", "enabled-ish", "", 5, None, ["ENABLED"]])
def test_invalid_status_fails_closed(value):
    p = PolicyRegistry({"x": {"discovery_status": value, "manual_import": True}}).get("x")
    assert p.status == SourceStatus.DISABLED and not p.automated_allowed


def test_status_is_case_insensitive_and_manual_flag_independent():
    p = PolicyRegistry({"x": {"discovery_status": "manual_only", "manual_import": True}}).get("x")
    assert p.status == SourceStatus.MANUAL_ONLY and p.manual_allowed and not p.automated_allowed
    p = PolicyRegistry({"x": {"discovery_status": "DISABLED", "manual_import": True}}).get("x")
    assert not p.manual_allowed  # DISABLED allows nothing, even manual import


def test_missing_or_garbled_policy_file_disables_everything(tmp_path):
    assert PolicyRegistry.load(path=tmp_path / "nope.yaml").get("career_pages").status == SourceStatus.DISABLED
    bad = tmp_path / "bad.yaml"
    bad.write_text("::: not [valid yaml", encoding="utf-8")
    assert PolicyRegistry.load(path=bad).get("linkedin").status == SourceStatus.DISABLED
    bad.write_text("- just\n- a list\n", encoding="utf-8")
    assert PolicyRegistry.load(path=bad).get("career_pages").status == SourceStatus.DISABLED


def test_malformed_numbers_disable_the_entry():
    p = PolicyRegistry({"x": {"discovery_status": "ENABLED", "requests_per_minute": "fast"}}).get("x")
    assert p.status == SourceStatus.DISABLED


def test_provider_can_never_be_more_permissive_than_its_source():
    reg = PolicyRegistry({"career_pages": {"discovery_status": "MANUAL_ONLY",
                                           "providers": {"greenhouse": {"discovery_status": "ENABLED"}}}})
    assert reg.get("career_pages", "greenhouse").status == SourceStatus.MANUAL_ONLY
    assert reg.get("career_pages", "unknown_provider").status == SourceStatus.DISABLED


def test_describe_lists_every_source_with_status(registry):
    rows = {r["source"]: r for r in registry.describe()}
    assert {"linkedin", "naukri", "indeed", "career_pages", "manual"} <= rows.keys()
    assert rows["linkedin"]["automated_discovery"] is False and rows["career_pages"]["automated_discovery"] is True
    assert rows["career_pages"]["providers"]["workday"]["status"] == "PENDING_POLICY_REVIEW"


# ============================================================ common adapter interface
def all_adapters():
    rec = Recorder()
    reg = PolicyRegistry.load(Settings())
    return build_adapters(Settings(), reg, client=rec.client())


def test_every_adapter_implements_the_common_interface():
    for name, a in all_adapters().items():
        assert isinstance(a, JobAdapter)
        for method in ("search", "fetch_details", "to_raw", "validate", "import_manual", "metadata", "require_discovery", "require_manual"):
            assert callable(getattr(a, method)), (name, method)
        md = a.metadata()
        assert {"source", "status", "automated_discovery", "manual_import", "requests_per_minute"} <= md.keys()
    with pytest.raises(TypeError):
        JobAdapter(policy())  # abstract: cannot be used directly
    for cls in (GreenhouseAdapter, LeverAdapter, GenericCareerPageAdapter, WorkdayAdapter, LinkedInAdapter):
        assert not inspect.isabstract(cls)


def test_validate_flags_unusable_records():
    a = ManualImportAdapter(policy())
    assert a.validate(RawJob(source="x", title="T", url="https://example.com/1")) == []
    assert "missing title" in a.validate(RawJob(source="x", url="https://example.com/1"))
    assert any("url" in p for p in a.validate(RawJob(source="x", title="T")))
    assert any("public" in p for p in a.validate(RawJob(source="x", title="T", url="http://localhost:8000/x")))


# ============================================================ permission enforcement per adapter
def test_instahyre_is_manual_only_and_only_accepts_its_own_links(registry):
    from integrations.instahyre.adapter import InstahyreAdapter

    pol = registry.get("instahyre")
    assert pol.status == SourceStatus.MANUAL_ONLY and pol.automated_allowed is False and pol.manual_allowed is True and pol.needs_human_confirmation
    a = InstahyreAdapter(pol)
    assert a.import_manual("https://www.instahyre.com/job-123-ml-engineer/", {"job_title": "ML Engineer"}).url.startswith("https://www.instahyre.com/")
    with pytest.raises(ValueError):
        a.import_manual("https://www.naukri.com/job-listings-x-150124000123", {"job_title": "T"})
    with pytest.raises(PolicyViolation):
        a.search(SearchQuery())


@pytest.mark.parametrize("cls", [LinkedInAdapter, IndeedAdapter, NaukriAdapter, ManualImportAdapter])
def test_manual_only_adapters_never_discover_even_if_policy_says_enabled(cls):
    forced = Policy(name=cls.name, status=SourceStatus.ENABLED, manual_import=True, requests_per_minute=60)
    a = cls(forced)  # note: no HTTP client at all
    with pytest.raises(PolicyViolation):
        a.search(SearchQuery())
    with pytest.raises(PolicyViolation):
        a.fetch_details(RawJob(source=cls.name, url="https://example.com/x"))
    assert a.metadata()["automated_discovery"] is False


@pytest.mark.parametrize("cls,status", [(LinkedInAdapter, "MANUAL_ONLY"), (NaukriAdapter, "PENDING_POLICY_REVIEW"), (IndeedAdapter, "DISABLED")])
def test_manual_import_requires_policy_permission(cls, status):
    a = cls(Policy(name=cls.name, status=SourceStatus(status), manual_import=(status != "DISABLED")))
    if status == "DISABLED":
        with pytest.raises(PolicyViolation):
            a.import_manual("https://www." + cls.host_suffixes[0] + "/x", {"job_title": "T"})
    else:
        assert a.import_manual(None, {"job_title": "Data Scientist", "company_name": "Acme"}).title == "Data Scientist"


@pytest.mark.parametrize("url,adapter,expected_id", [
    ("https://www.linkedin.com/jobs/view/3912345678/?trackingId=abc", LinkedInAdapter, "3912345678"),
    ("https://in.linkedin.com/jobs/view/senior-ml-engineer-at-acme-3912345678", LinkedInAdapter, "3912345678"),
    ("https://www.linkedin.com/jobs/search/?currentJobId=3912345678&keywords=ml", LinkedInAdapter, "3912345678"),
    ("https://in.indeed.com/viewjob?jk=a1b2c3d4e5f60718", IndeedAdapter, "a1b2c3d4e5f60718"),
    ("https://www.naukri.com/job-listings-ml-engineer-acme-pune-3-to-6-years-150124000123?src=x", NaukriAdapter, "150124000123"),
    ("https://www.naukri.com/job-listings-ml-engineer-acme-pune-3-to-6-years-150124000123", NaukriAdapter, "150124000123"),   # the clean link the dashboard sends
])
def test_manual_import_extracts_job_id_without_any_request(url, adapter, expected_id):
    a = adapter(Policy(name=adapter.name, status=SourceStatus.MANUAL_ONLY, manual_import=True))
    raw = a.import_manual(url, {"job_title": "ML Engineer", "company_name": "Acme", "job_description": "x" * 50})
    assert raw.source_job_id == expected_id and raw.url == url and raw.original["manual_import"] is True


@pytest.mark.parametrize("url", ["https://evil.example/jobs/view/3912345678", "javascript:alert(1)", "ftp://linkedin.com/x",
                                 "http://127.0.0.1/jobs/view/3912345678", "https://user:pw@linkedin.com/jobs/view/1"])
def test_manual_import_rejects_foreign_or_unsafe_urls(url):
    a = LinkedInAdapter(Policy(name="linkedin", status=SourceStatus.MANUAL_ONLY, manual_import=True))
    with pytest.raises(ValueError):
        a.import_manual(url, {"job_title": "T"})


def test_manual_import_needs_url_or_details():
    a = ManualImportAdapter(Policy(name="manual", status=SourceStatus.MANUAL_ONLY, manual_import=True))
    with pytest.raises(ValueError):
        a.import_manual(None, {})
    raw = a.import_manual("https://careers.example.org/jobs/9", {"job_title": "Analyst"})
    assert raw.source == "manual" and raw.title == "Analyst"


# ============================================================ HTTP guard: policy, hosts, blocks, limits
GH = "https://boards-api.greenhouse.io/v1/boards/acme/jobs"


def test_disabled_source_makes_no_request():
    for status in ("DISABLED", "MANUAL_ONLY", "PENDING_POLICY_REVIEW"):
        rec = Recorder({GH: json_response({})})
        with pytest.raises(PolicyViolation):
            http_for(rec, policy(status)).get(GH)
        assert rec.requests == []


@pytest.mark.parametrize("url", [
    "http://boards-api.greenhouse.io/v1/boards/acme/jobs",          # not https
    "https://evil.example/v1/boards/acme/jobs",                     # host not allow-listed
    "https://boards-api.greenhouse.io.evil.example/x",              # look-alike host
    "https://127.0.0.1/x", "https://localhost/x", "https://10.0.0.5/x", "https://169.254.169.254/latest/meta-data",
    "https://user:pw@boards-api.greenhouse.io/x", "file:///etc/passwd",
])
def test_host_and_scheme_allow_list(url):
    rec = Recorder({"": json_response({})})
    with pytest.raises(PolicyViolation):
        http_for(rec, policy(hosts=("boards-api.greenhouse.io", "127.0.0.1", "localhost", "10.0.0.5", "169.254.169.254"))).get(url)
    assert rec.requests == []


@pytest.mark.parametrize("status", [401, 403, 429, 451])
def test_refusals_stop_the_source_and_are_never_retried(status):
    rec = Recorder({GH: httpx.Response(status, text="no")})
    with pytest.raises(SourceBlocked):
        http_for(rec, policy()).get(GH)
    assert len(rec.requests) == 1


@pytest.mark.parametrize("body", ["<html>Please complete the CAPTCHA to continue</html>", "<html>Are you a robot?</html>",
                                  "<html>Please verify you are human</html>", "<html>Sign in to continue authwall</html>"])
def test_bot_check_pages_are_treated_as_blocks(body):
    rec = Recorder({GH: html_response(body)})
    with pytest.raises(SourceBlocked):
        http_for(rec, policy()).get(GH, accept="text/html")
    assert len(rec.requests) == 1


def test_server_errors_timeouts_and_network_errors_are_sanitised():
    for route, msg in [(httpx.Response(503, text="secret internal trace"), "server error"),
                       (httpx.ReadTimeout("t"), "timed out"), (httpx.ConnectError("dns secret.internal failed"), "Network error")]:
        rec = Recorder({GH: route})
        with pytest.raises(AdapterError) as e:
            http_for(rec, policy()).get(GH)
        assert msg in str(e.value) and "secret" not in str(e.value)


def test_redirects_are_revalidated_each_hop():
    rec = Recorder({GH: httpx.Response(302, headers={"location": "https://evil.example/steal"})})
    with pytest.raises(PolicyViolation):
        http_for(rec, policy()).get(GH)
    assert len(rec.requests) == 1  # the redirect target was never requested
    ok = Recorder({GH: httpx.Response(302, headers={"location": "/v1/boards/acme/jobs2"}),
                   GH + "2": json_response({"ok": 1})})
    assert http_for(ok, policy()).get(GH).json() == {"ok": 1}


def test_oversized_responses_are_rejected():
    rec = Recorder({GH: httpx.Response(200, content=b"x" * 6_000_000, headers={"content-type": "application/json"})})
    with pytest.raises(AdapterError):
        http_for(rec, policy()).get(GH)


def test_requests_carry_an_honest_user_agent_and_no_credentials():
    rec = Recorder({GH: json_response({"jobs": []})})
    http_for(rec, policy()).get(GH)
    h = rec.requests[0].headers
    assert h["user-agent"] == UA and "authorization" not in h and "cookie" not in h


def test_private_host_helpers():
    assert all(is_private_host(h) for h in ("localhost", "127.0.0.1", "10.1.2.3", "192.168.0.9", "[::1]", "169.254.169.254", "svc.internal", None))
    assert not is_private_host("boards-api.greenhouse.io") and not is_private_host("8.8.8.8")
    assert public_http_url("https://jobs.example.com/a") and not public_http_url("http://localhost/a") and not public_http_url("javascript:1")


# ============================================================ rate limiting
class FakeTime:
    def __init__(self):
        self.now, self.sleeps = 100.0, []

    def clock(self):
        return self.now

    def sleep(self, s):
        self.sleeps.append(round(s, 3))
        self.now += s


def test_rate_limiter_spaces_requests():
    t = FakeTime()
    rl = RateLimiter(30, clock=t.clock, sleep=t.sleep)  # 30/min => one every 2s
    for _ in range(4):
        rl.acquire()
    assert t.sleeps == [2.0, 2.0, 2.0]
    t.now += 10  # a long pause: no waiting needed
    rl.acquire()
    assert len(t.sleeps) == 3


def test_request_budget_is_enforced():
    rl = RateLimiter(600, max_requests=3, clock=lambda: 0.0, sleep=lambda s: None)
    for _ in range(3):
        rl.acquire()
    with pytest.raises(RateLimitExceeded):
        rl.acquire()


def test_zero_rate_means_no_requests():
    with pytest.raises(RateLimitExceeded):
        RateLimiter(0).acquire()


def test_http_layer_applies_rate_limit_and_budget():
    t = FakeTime()
    rec = Recorder({GH: json_response({"jobs": []})})
    h = http_for(rec, policy(rpm=12, budget=3), sleep=t.sleep, clock=t.clock)  # one per 5s, budget 3
    for _ in range(3):
        h.get(GH)
    assert t.sleeps == [5.0, 5.0] and len(rec.requests) == 3
    with pytest.raises(RateLimitExceeded):
        h.get(GH)
    assert len(rec.requests) == 3  # the 4th request was never sent


# ============================================================ Greenhouse / Lever / Workday / generic
def gh_adapter(rec, **kw):
    pol = policy(hosts=("boards-api.greenhouse.io",), **kw)
    return GreenhouseAdapter(pol, http_for(rec, pol), [company(ats="greenhouse", board_token="acmeanalytics")])


def test_greenhouse_search_maps_the_response():
    rec = Recorder({GH.replace("acme", "acmeanalytics"): json_response(GREENHOUSE_LIST)})
    res = gh_adapter(rec).search(SearchQuery())
    assert len(res.jobs) == 2 and rec.requests[0].url.params["content"] == "true"
    j = res.jobs[0]
    assert (j.source, j.provider, j.source_job_id, j.company) == ("company_career_page", "greenhouse", "101", "Acme Analytics")
    assert j.title == "Machine Learning Engineer (m/f/d)" and j.location_text == ["Pune, India"] and j.department == "Engineering"
    assert "<p>" not in j.description and "- Design and deploy" in j.description  # escaped HTML -> readable bullets
    assert j.original["board_token"] == "acmeanalytics" and j.posted_raw == "2026-11-01T08:30:00-05:00"


def test_greenhouse_edge_cases():
    base = GH.replace("acme", "acmeanalytics")
    assert "not found" in gh_adapter(Recorder({base: httpx.Response(404)})).search(SearchQuery()).notes[0]
    with pytest.raises(AdapterError):
        gh_adapter(Recorder({base: httpx.Response(200, text="<html>not json")})).search(SearchQuery())
    with pytest.raises(AdapterError):
        gh_adapter(Recorder({base: json_response({"jobs": "oops"})})).search(SearchQuery())
    rec = Recorder({})
    a = GreenhouseAdapter(policy(), http_for(rec, policy()), [CompanySource.model_construct(name="X", board_token="../evil", ats="greenhouse")])
    with pytest.raises(AdapterError):
        a.search(SearchQuery())
    assert rec.requests == []  # an unsafe token never reaches the network
    with pytest.raises(Exception):
        CompanySource(name="X", board_token="a/b")


def test_greenhouse_fetch_details_and_gone():
    rec = Recorder({"https://boards-api.greenhouse.io/v1/boards/acmeanalytics/jobs/101": json_response(GREENHOUSE_LIST["jobs"][0])})
    a = gh_adapter(rec)
    raw = a.fetch_details(RawJob(source="company_career_page", provider="greenhouse", source_job_id="101", company="Acme Analytics",
                                 original={"board_token": "acmeanalytics"}))
    assert raw.title.startswith("Machine Learning Engineer")
    from integrations.base_adapter import JobGone
    with pytest.raises(JobGone):
        gh_adapter(Recorder({})).fetch_details(RawJob(source="c", source_job_id="9", original={"board_token": "acmeanalytics"}))
    with pytest.raises(AdapterError):
        a.fetch_details(RawJob(source="c", source_job_id="../x", original={"board_token": "acmeanalytics"}))


def test_provider_adapters_refuse_when_policy_not_enabled():
    rec = Recorder({})
    a = GreenhouseAdapter(policy("PENDING_POLICY_REVIEW"), http_for(rec, policy()), [company(board_token="x")])
    with pytest.raises(PolicyViolation):
        a.search(SearchQuery())
    assert rec.requests == []


def test_lever_search_maps_the_response():
    pol = policy(hosts=("api.lever.co",))
    rec = Recorder({"https://api.lever.co/v0/postings/betalabs": json_response(LEVER_LIST)})
    a = LeverAdapter(pol, http_for(rec, pol), [company(name="Beta Labs", ats="lever", board_token="betalabs")])
    res = a.search(SearchQuery())
    j = res.jobs[0]
    assert rec.requests[0].url.params["mode"] == "json"
    assert (j.provider, j.title, j.company, j.work_mode_text, j.employment_text) == ("lever", "Senior Data Scientist", "Beta Labs", "hybrid", "Full-time")
    assert "5+ years of experience in data science" in j.description and (j.salary_min, j.salary_max, j.salary_currency) == (3000000, 4000000, "INR")
    assert j.posted_raw == 1762070400000
    bad = LeverAdapter(pol, http_for(Recorder({"https://api.lever.co/v0/postings/betalabs": json_response({"oops": 1})}), pol),
                       [company(board_token="betalabs")])
    with pytest.raises(AdapterError):
        bad.search(SearchQuery())


def test_workday_is_detected_but_never_extracted():
    w = WorkdayAdapter(policy("PENDING_POLICY_REVIEW"))
    with pytest.raises(PolicyViolation):
        w.search(SearchQuery())
    with pytest.raises(PolicyViolation):
        w.to_raw({})
    assert WorkdayAdapter(policy("ENABLED")).metadata()["automated_discovery"] is False  # no code path exists


ROBOTS_ALLOW = "User-agent: *\nDisallow: /private/\n"
ROBOTS_DENY = "User-agent: *\nDisallow: /\n"


def generic(rec, comp, rpm=600):
    pol = Policy(name="generic_page", status=SourceStatus.ENABLED, allowed_hosts=[], requests_per_minute=rpm, require_robots_txt=True)
    return GenericCareerPageAdapter(pol, http_for(rec, pol, extra=("careers.gamma.example",)), [comp], user_agent=UA)


GAMMA = "https://careers.gamma.example"


def test_generic_page_reads_only_json_ld_when_approved_and_robots_allows():
    rec = Recorder({f"{GAMMA}/robots.txt": httpx.Response(200, text=ROBOTS_ALLOW, headers={"content-type": "text/plain"}),
                    f"{GAMMA}/jobs/backend-engineer": html_response(JSONLD_PAGE), f"{GAMMA}/jobs/data-analyst": html_response("<html>no data</html>"),
                    f"{GAMMA}/jobs": html_response(LISTING_PAGE)})
    a = generic(rec, company(name="Gamma Corp", ats="generic", careers_url=f"{GAMMA}/jobs", approved=True))
    res = a.search(SearchQuery())
    assert [j.title for j in res.jobs] == ["Backend Engineer"]
    j = res.jobs[0]
    assert (j.provider, j.company, j.location_text, j.employment_text, j.source_job_id) == ("generic", "Gamma Corp", ["Pune, MH, IN"], "FULL_TIME", "GC-77")
    assert (j.salary_min, j.salary_max, j.salary_currency, j.posted_raw) == (1800000, 2600000, "INR", "2026-11-05")
    assert not any("other.example" in u for u in rec.urls)  # links to other hosts are never followed
    assert any("no structured JobPosting data" in n for n in res.notes)


def test_generic_page_needs_approval():
    rec = Recorder({})
    res = generic(rec, company(name="Gamma", ats="generic", careers_url=f"{GAMMA}/jobs", approved=False)).search(SearchQuery())
    assert res.jobs == [] and "not approved" in res.notes[0] and rec.requests == []


@pytest.mark.parametrize("robots", [httpx.Response(200, text=ROBOTS_DENY, headers={"content-type": "text/plain"}),
                                    httpx.Response(500, text="oops"), httpx.Response(403, text="no"), httpx.ConnectError("x")])
def test_generic_page_fails_closed_on_robots(robots):
    rec = Recorder({f"{GAMMA}/robots.txt": robots, f"{GAMMA}/jobs": html_response(JSONLD_PAGE)})
    res = generic(rec, company(name="Gamma", ats="generic", careers_url=f"{GAMMA}/jobs", approved=True)).search(SearchQuery())
    assert res.jobs == [] and "robots.txt" in res.notes[0]
    assert not any(u.endswith("/jobs") for u in rec.urls)  # the careers page itself was never fetched


def test_generic_page_without_robots_txt_is_allowed():
    rec = Recorder({f"{GAMMA}/robots.txt": httpx.Response(404), f"{GAMMA}/jobs": html_response(JSONLD_PAGE)})
    res = generic(rec, company(name="Gamma", ats="generic", careers_url=f"{GAMMA}/jobs", approved=True)).search(SearchQuery())
    assert len(res.jobs) == 1


def test_generic_page_respects_disallowed_paths_and_sitemaps():
    sitemap = f"<urlset><url><loc>{GAMMA}/jobs/backend-engineer</loc></url><url><loc>{GAMMA}/private/jobs/secret</loc></url><url><loc>{GAMMA}/about</loc></url></urlset>"
    rec = Recorder({f"{GAMMA}/robots.txt": httpx.Response(200, text=ROBOTS_ALLOW, headers={"content-type": "text/plain"}),
                    f"{GAMMA}/sitemap.xml": httpx.Response(200, text=sitemap, headers={"content-type": "application/xml"}),
                    f"{GAMMA}/jobs/backend-engineer": html_response(JSONLD_PAGE), f"{GAMMA}/private/jobs/secret": html_response(JSONLD_PAGE)})
    res = generic(rec, company(name="Gamma", ats="generic", careers_url=f"{GAMMA}/jobs", approved=True, sitemap=True)).search(SearchQuery())
    assert len(res.jobs) == 1 and not any("/private/" in u for u in rec.urls) and not any(u.endswith("/about") for u in rec.urls)


def test_json_ld_extraction_handles_graphs_lists_and_garbage():
    page = '<script type="application/ld+json">{"@graph":[{"@type":"Organization"},{"@type":["JobPosting"],"title":"A"}]}</script>' \
           '<script type="application/ld+json">{broken json</script><script type="application/ld+json">[{"@type":"JobPosting","title":"B"}]</script>'
    assert [p["title"] for p in extract_job_postings(page)] == ["A", "B"]
    assert extract_job_postings("<html></html>") == []


# ============================================================ career pages orchestrator
def career(rec, companies, reg=None):
    reg = reg or PolicyRegistry.load(Settings())
    adapters = build_adapters(Settings(), reg, client=rec.client())
    ca = adapters["career_pages"]
    ca.companies = companies
    return ca


def test_career_pages_with_no_companies_does_nothing():
    rec = Recorder({})
    res = career(rec, []).search(SearchQuery())
    assert res.jobs == [] and "No companies" in res.notes[0] and rec.requests == []


def test_career_pages_routes_by_provider_and_detects_from_url():
    rec = Recorder({"https://boards-api.greenhouse.io/v1/boards/acmeanalytics/jobs": json_response(GREENHOUSE_LIST),
                    "https://api.lever.co/v0/postings/betalabs": json_response(LEVER_LIST)})
    ca = career(rec, [company(careers_url="https://boards.greenhouse.io/acmeanalytics"),
                      company(name="Beta Labs", careers_url="https://jobs.lever.co/betalabs")])
    res = ca.search(SearchQuery())
    assert sorted(j.provider for j in res.jobs) == ["greenhouse", "greenhouse", "lever"]
    assert all(u.startswith(("https://boards-api.greenhouse.io/", "https://api.lever.co/")) for u in rec.urls)


def test_career_pages_workday_and_unknown_providers_are_skipped_not_fetched():
    rec = Recorder({})
    ca = career(rec, [company(name="Big Co", careers_url="https://bigco.wd5.myworkdayjobs.com/en-US/External"),
                      company(name="Mystery", ats=None, careers_url=None)])
    res = ca.search(SearchQuery())
    assert res.jobs == [] and rec.requests == []
    assert any("Big Co" in n and "policy" in n.lower() for n in res.notes) and any("Mystery" in n for n in res.notes)


def test_blocked_company_does_not_stop_others_and_is_not_retried():
    rec = Recorder({"https://boards-api.greenhouse.io/v1/boards/blocked/jobs": httpx.Response(403),
                    "https://api.lever.co/v0/postings/betalabs": json_response(LEVER_LIST)})
    ca = career(rec, [company(name="Blocked Co", ats="greenhouse", board_token="blocked"),
                      company(name="Beta Labs", ats="lever", board_token="betalabs")])
    res = ca.search(SearchQuery())
    assert res.blocked and len(res.jobs) == 1 and "refused" in res.errors[0]
    assert sum("blocked" in u for u in rec.urls) == 1


def test_company_filter_and_job_cap():
    rec = Recorder({"https://boards-api.greenhouse.io/v1/boards/acmeanalytics/jobs": json_response(GREENHOUSE_LIST)})
    ca = career(rec, [company(ats="greenhouse", board_token="acmeanalytics"), company(name="Other", ats="greenhouse", board_token="other")])
    assert len(ca.search(SearchQuery(companies=["acme analytics"])).jobs) == 2
    assert not any("/other/" in u for u in rec.urls)


def test_title_filter_is_applied_before_the_job_cap_so_a_large_board_cannot_starve_the_others():
    big = {"jobs": [{"id": i, "title": "Sales Manager", "absolute_url": f"https://boards.greenhouse.io/big/jobs/{i}"} for i in range(400)]}
    rec = Recorder({"https://boards-api.greenhouse.io/v1/boards/big/jobs": json_response(big),
                    "https://boards-api.greenhouse.io/v1/boards/acmeanalytics/jobs": json_response(GREENHOUSE_LIST)})
    ca = career(rec, [company(name="Big Co", ats="greenhouse", board_token="big"), company(ats="greenhouse", board_token="acmeanalytics")])
    res = ca.search(SearchQuery(titles=["machine learning engineer"]))
    assert [j.title for j in res.jobs] == ["Machine Learning Engineer (m/f/d)"]          # the second company was still read
    assert any("Big Co: 400 of 400" in n for n in res.notes) and not any("cap" in n.lower() for n in res.notes)
    every = ca.search(SearchQuery()).jobs                                                  # no title filter: every company is still listed (one request each);
    assert len(every) == 400 + len(GREENHOUSE_LIST["jobs"])                                # the per-run cap is applied by the engine to jobs that pass the filters


def test_response_size_limit_comes_from_the_policy_and_is_bounded():
    body = {"jobs": [{"id": 1, "title": "ML Engineer", "content": "x" * 6_000_000}]}
    url = "https://boards-api.greenhouse.io/v1/boards/acmeanalytics/jobs"
    with pytest.raises(AdapterError, match="too large"):                                   # default 5 MB limit
        http_for(Recorder({url: json_response(body)}), policy()).get(url)
    reg = PolicyRegistry.load(Settings())
    assert reg.get("career_pages", "greenhouse").max_response_bytes == 40_000_000 and reg.get("career_pages", "generic_page").max_response_bytes == 5_000_000
    ca = career(Recorder({url: json_response(body)}), [company(ats="greenhouse", board_token="acmeanalytics")])
    assert len(ca.search(SearchQuery()).jobs) == 1 and ca.search(SearchQuery()).errors == []
    too_big = PolicyRegistry({"x": {"discovery_status": "ENABLED", "max_response_bytes": 10**9}}).get("x")
    assert too_big.status == SourceStatus.DISABLED                                         # above the ceiling = invalid entry = disabled


def test_career_pages_disabled_source_blocks_everything():
    reg = PolicyRegistry({"career_pages": {"discovery_status": "DISABLED"}})
    rec = Recorder({})
    ca = career(rec, [company(ats="greenhouse", board_token="acmeanalytics")], reg=reg)
    with pytest.raises(PolicyViolation):
        ca.search(SearchQuery())
    assert rec.requests == []
