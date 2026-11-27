"""Instahyre and Glassdoor job-alert e-mails from the candidate's own Gmail. The e-mails are made up in the layouts seen in real alerts;
the personal sign-in token / tracking ids in their links must never be stored. Nothing contacts either site."""
import json
from urllib.parse import quote

import pytest

from integrations.email.alerts_adapter import PARSERS
from integrations.email.glassdoor_alerts import parse_glassdoor_alert
from integrations.email.instahyre_alerts import instahyre_job_ref, parse_instahyre_alert

pytestmark = pytest.mark.usefixtures("no_network")


def _ih(slug):
    target = f"https://www.instahyre.com/{slug}/?url_auth_token=SECRETTOKEN123%3Aabc&utm_source=alert"
    return "https://click4.instahyre.com/CL0/" + quote(target, safe="") + "/1/0109abc"


INSTAHYRE = f"""<html><head><title>x</title><style>.a{{}}</style></head><body>
<a href="https://click4.instahyre.com/CL0/https:%2F%2Fwww.instahyre.com/1/0109"><img alt="Instahyre"></a>
<p>Hi Jane,</p><p>Here are some active job openings we have that match your saved search.</p>
<a href="{_ih('job-474583-generative-ai-engineer-at-acme-bangalore-hyderabad')}"><table><tr><td><div>Generative AI Engineer</div></td></tr>
 <tr><td><div><span>Acme Shaw</span> <table class="chip"><tr><td>Actively hiring</td></tr></table></div></td></tr><tr><td><div>Bangalore,Hyderabad</div></td></tr>
 <tr><td><a href="{_ih('job-474583-generative-ai-engineer-at-acme-bangalore-hyderabad')}">Apply here</a></td></tr></table></a>
<p>Acme Shaw is an investment and technology firm.</p><div>LLM</div><div>LangChain</div>
<a href="{_ih('job-462833-sr-ml-engineer-at-beta-work-from-home')}"><div>Sr. ML Engineer</div><div>Beta Health</div><div>Work From Home</div>
 <a href="{_ih('job-462833-sr-ml-engineer-at-beta-work-from-home')}">Apply here</a></a>
<p>Beta Health provides connectivity.</p><div>Python</div><p>Thanks,</p><p>The Instahyre Team</p></body></html>"""


def test_instahyre_cards_give_title_company_places_and_a_clean_link_without_the_sign_in_token():
    jobs, _ = parse_instahyre_alert(INSTAHYRE)
    assert [(j.source_job_id, j.title, j.company, j.location_text, j.work_mode_text) for j in jobs] == [
        ("474583", "Generative AI Engineer", "Acme Shaw", ["Bangalore", "Hyderabad"], None),
        ("462833", "Sr. ML Engineer", "Beta Health", [], "Remote")]
    assert jobs[0].url == "https://www.instahyre.com/job-474583-generative-ai-engineer-at-acme-bangalore-hyderabad/"
    assert jobs[0].source == "instahyre" and jobs[0].description is None            # no job description in an alert: stored unscored
    dump = json.dumps([j.model_dump() for j in jobs])
    assert "SECRETTOKEN" not in dump and "url_auth_token" not in dump and "Jane" not in dump and "click4" not in dump
    assert instahyre_job_ref("https://www.instahyre.com.evil.example/job-1234-x/") is None


def _gd(n):
    return f"https://www.glassdoor.com/partner/jobListing.htm?pos=10{n}&ao=1136043&s=58&guid=0000019abcdef{n}&src=GD_JOB_AD&t=JA&vt=e&uido=SECRETUSER&ja=999"


GLASSDOOR = f"""<html><body><table>
<tr><td><a href="{_gd(1)}"><table><tr><td><img alt=""></td><td><p>Acme Robotics</p><p>4.0 ★</p></td></tr><tr><td><p>Senior Machine learning Engineer</p>
 <p>Bengaluru</p><p>₹779K - ₹911K (Glassdoor est.)</p><p>Easy Apply</p><p>1d</p></td></tr></table></a></td></tr>
<tr><td><a href="{_gd(2)}"><div>Refold Example</div><div>Forward Deployed Engineer</div><div>Remote</div><div>4d</div></a></td></tr>
<tr><td><a href="{_gd(1)}"><div>Acme Robotics</div><div>4.0 ★</div><div>Senior Machine learning Engineer</div><div>Bengaluru</div></a></td></tr>
<tr><td><a href="https://www.glassdoor.com/Job/pune-jobs-SRCH_IL.0,4_IC2856202.htm?uvk=SECRET">See more jobs</a></td></tr>
<tr><td>This message was sent to <a href="mailto:jane.sample@example.com">jane.sample@example.com</a> <a href="https://www.glassdoor.com/member/account/emailSettings_input.htm">Manage Settings</a></td></tr>
</table></body></html>"""


def test_glassdoor_jobs_get_details_a_stable_id_and_a_search_link_never_the_tracking_link():
    jobs, _ = parse_glassdoor_alert(GLASSDOOR)
    assert [(j.title, j.company, j.location_text, j.work_mode_text, j.salary_text, j.posted_raw) for j in jobs] == [
        ("Senior Machine learning Engineer", "Acme Robotics", ["Bengaluru"], None, "₹779K - ₹911K (Glassdoor est.)", "1 day ago"),
        ("Forward Deployed Engineer", "Refold Example", [], "Remote", None, "4 days ago")]                 # the repeated job is kept once
    assert jobs[0].url == "https://www.glassdoor.co.in/Job/jobs.htm?sc.keyword=Senior+Machine+learning+Engineer+Acme+Robotics"
    assert jobs[0].source_job_id == parse_glassdoor_alert(GLASSDOOR)[0][0].source_job_id.lower() and jobs[0].source_job_id.startswith("gd-")
    dump = json.dumps([j.model_dump() for j in jobs], ensure_ascii=False)
    for never in ("guid", "uido", "SECRETUSER", "jobListing", "uvk", "jane.sample", "Manage Settings"):
        assert never not in dump


def test_both_sites_are_read_from_gmail():
    assert PARSERS["instahyre.com"] is parse_instahyre_alert and PARSERS["glassdoor.com"] is parse_glassdoor_alert


def test_instahyre_skill_chips_are_kept_as_listed_skills_and_the_company_blurb_is_not():
    jobs, _ = parse_instahyre_alert(INSTAHYRE)
    assert jobs[0].original["listed_skills"] == ["LLM", "LangChain"] and jobs[1].original["listed_skills"] == ["Python"]
    assert all("investment" not in s for j in jobs for s in j.original["listed_skills"])
