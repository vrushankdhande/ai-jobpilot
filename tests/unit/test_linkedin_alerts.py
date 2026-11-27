"""LinkedIn job-alert e-mails read from the candidate's own Gmail. The e-mails below are made up in the shape of real alerts ("Your job
alert for ..."); connection names are invented and must never end up in a job. Nothing contacts LinkedIn."""
import json
from urllib.parse import quote

import pytest

from config.settings import Settings
from engines.discovery.job_normalizer import normalize
from engines.matching.skill_extractor import get_catalog
from integrations.email.alerts_adapter import PARSERS, SEARCH_FROM
from integrations.email.linkedin_alerts import linkedin_job_ref, parse_linkedin_alert

pytestmark = pytest.mark.usefixtures("no_network")

L = "https://www.linkedin.com/comm/jobs/view/{}/?trackingId=abc%3D%3D&refId=xyz&lipi=urn%3Ali&midToken=tok"

# shape 1: logo link (company name as alt/text), title link, "Company · Location" line, then a person and their connections
SEPARATE = f"""<html><body><h1>Your job alert for machine learning engineer</h1><table>
<tr><td><a href="{L.format(4300000001)}"><img alt="Nielsen">Nielsen</a></td></tr>
<tr><td><a href="{L.format(4300000001)}">AI/ML Data Scientist - (P3/ P4 )</a></td></tr>
<tr><td><p>Nielsen · Bengaluru</p></td></tr>
<tr><td><a href="https://www.linkedin.com/comm/in/jane-sample">Jane Sample</a><p>5 connections</p></td></tr>
<tr><td><a href="{L.format(4300000002)}">Solifi</a></td></tr>
<tr><td><a href="{L.format(4300000002)}">Senior AI Engineer</a></td></tr>
<tr><td><p>Solifi · Bengaluru (Hybrid)</p><p>Actively recruiting</p></td></tr>
<tr><td><a href="https://www.linkedin.com/comm/jobs/search?keywords=ml">See all jobs</a></td></tr>
<tr><td>You are receiving Job Alert emails. <a href="https://www.linkedin.com/comm/jobs/alerts">Manage job alerts</a> · Unsubscribe</td></tr>
</table></body></html>"""

# shape 2: the whole card is one link
CARD = f"""<html><body><table>
<tr><td><a href="{L.format(4300000003)}"><table><tr><td><img alt="Persistent Systems"></td><td><p>Generative AI Engineer</p>
<p>Persistent Systems · Bengaluru (On-site)</p><p>Actively recruiting</p></td></tr></table></a></td></tr>
<tr><td><a href="https://www.linkedin.com/redir/redirect?url={quote(L.format(4300000004), safe='')}"><div>GenAI Engineer</div><div>Siemens Healthineers · Bengaluru</div>
<div>Rahul Example</div><div>1 connection</div></a></td></tr>
</table></body></html>"""


def test_job_links_are_recognised_directly_and_inside_redirects_but_never_from_look_alikes():
    assert linkedin_job_ref(L.format(4300000001)) == ("4300000001", "https://www.linkedin.com/jobs/view/4300000001/")
    assert linkedin_job_ref("https://www.linkedin.com/jobs/view/senior-ai-engineer-at-solifi-4300000002/")[0] == "4300000002"
    assert linkedin_job_ref("https://www.linkedin.com/redir/redirect?url=" + quote(L.format(4300000004), safe=""))[0] == "4300000004"
    assert linkedin_job_ref("https://linkedin.com.evil.example/jobs/view/4300000001/") is None
    assert linkedin_job_ref("https://www.linkedin.com/comm/in/jane-sample") is None


def test_separate_links_give_title_company_location_and_the_clean_link():
    jobs, hidden = parse_linkedin_alert(SEPARATE)
    assert [(j.source_job_id, j.title, j.company, j.location_text, j.work_mode_text) for j in jobs] == [
        ("4300000001", "AI/ML Data Scientist - (P3/ P4 )", "Nielsen", ["Bengaluru"], None),
        ("4300000002", "Senior AI Engineer", "Solifi", ["Bengaluru (Hybrid)"], "Hybrid")]
    assert jobs[0].url == "https://www.linkedin.com/jobs/view/4300000001/" and jobs[0].source == "linkedin" and jobs[0].provider == "gmail_alert"
    assert hidden == 0


def test_a_whole_card_link_is_split_into_its_parts():
    jobs, _ = parse_linkedin_alert(CARD)
    assert [(j.title, j.company, j.location_text, j.work_mode_text) for j in jobs] == [
        ("Generative AI Engineer", "Persistent Systems", ["Bengaluru (On-site)"], "On-site"),
        ("GenAI Engineer", "Siemens Healthineers", ["Bengaluru"], None)]


def test_connection_names_and_footer_text_are_never_kept():
    every = json.dumps([j.model_dump() for j in parse_linkedin_alert(SEPARATE)[0] + parse_linkedin_alert(CARD)[0]])
    for never in ("Jane Sample", "Rahul Example", "connections", "connection", "Actively recruiting", "Unsubscribe", "trackingId", "midToken"):
        assert never not in every


def test_alert_jobs_normalise_like_other_jobs_and_only_the_alert_address_is_searched():
    rec = normalize(parse_linkedin_alert(SEPARATE)[0][1], get_catalog(Settings()))
    assert rec.source == "linkedin" and rec.source_job_id == "4300000002" and rec.work_mode == "hybrid" and "bengaluru" in rec.location_keys
    assert "linkedin.com" in PARSERS and SEARCH_FROM["linkedin.com"] == "jobalerts-noreply@linkedin.com"
