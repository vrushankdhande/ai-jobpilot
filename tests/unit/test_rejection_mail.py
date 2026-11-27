"""Rejection e-mails: recognising them, reading company/role, and matching them to an application. Made-up e-mails; no network."""
import pytest

from backend.services.rejection_service import gmail_query
from engines.application.rejection_matcher import company_core, match_rejection
from integrations.email.rejection_mail import company_from_sender, detect_rejection, text_for_matching

pytestmark = pytest.mark.usefixtures("no_network")


def _d(subject, text, domain="example.com", name=""):
    return detect_rejection(subject, text, sender_domain=domain, sender_name=name)


def test_linkedin_style_subject_gives_role_and_company():
    s = _d("Your application to Machine Learning Engineer at Acme Analytics",
           "Your update from Acme Analytics\nUnfortunately, we will not be moving forward with your application.", "linkedin.com", "LinkedIn")
    assert (s.company, s.role) == ("Acme Analytics", "Machine Learning Engineer")
    assert "not be moving forward" in s.phrase


def test_ats_mail_company_from_body_and_sender():
    s = _d("Update on your application", "Thank you for your interest in Beta Labs. After careful consideration, we have decided to move forward "
           "with other candidates for the Data Scientist position.\nThe Beta Labs Talent Acquisition Team", "greenhouse-mail.io", "Beta Labs Recruiting")
    assert s.company == "Beta Labs" and s.role == "Data Scientist" and s.company_from_sender == "Beta Labs"


def test_naukri_not_shortlisted():
    s = _d("Application status: Not shortlisted", "Your application for the GenAI Developer job at Delta Tech has not been shortlisted.", "naukri.com")
    assert (s.company, s.role) == ("Delta Tech", "GenAI Developer")


def test_role_only_and_company_from_domain():
    s = _d("Regarding the Senior AI Engineer role", "Thanks for applying. We regret to inform you that your application has not been shortlisted.", "gamma.ai", "Gamma HR")
    assert s.role == "Senior AI Engineer" and s.company is None and s.company_from_sender == "Gamma"


@pytest.mark.parametrize("subject,text", [
    ("Weekly newsletter", "Unfortunately the event is cancelled."),                                    # no application context
    ("Interview invitation", "Thank you for applying. We would like to invite you to an interview."),  # good news
    ("New jobs for you", "10 new jobs match your profile. Apply now."),
])
def test_not_a_rejection(subject, text):
    assert _d(subject, text) is None


def test_personal_lines_and_quoted_reply_are_not_used():
    s = _d("Your application", "Unfortunately we are not moving forward with your application for the Data Analyst role at Zeta.\n"
           "Call me on +91 98765 43210 or jane@zeta.com\n> On Mon, you wrote: I am applying for the Python role at Other Co")
    text = text_for_matching("Your application", "x\nCall +91 98765 43210\n> quoted Other Co")
    assert s.company == "Zeta" and "98765" not in text and "other co" not in text


def test_sender_hints():
    assert company_from_sender("Acme Careers", "acme.com") == "Acme"
    assert company_from_sender("", "careers.omega.co.in") == "omega"
    assert company_from_sender("LinkedIn", "linkedin.com") is None
    assert company_from_sender("no-reply", "myworkday.com") is None


def test_company_core():
    assert company_core("Acme Analytics Pvt. Ltd.") == "acme analytics"
    assert company_core("The Beta Company Inc") == "beta"


APPS = [
    {"application_id": "a1", "company_name": "Acme Analytics Pvt Ltd", "job_title": "Machine Learning Engineer", "state": "SUBMITTED"},
    {"application_id": "a2", "company_name": "Acme Analytics Pvt Ltd", "job_title": "Data Engineer", "state": "VERIFIED"},
    {"application_id": "b1", "company_name": "Beta Labs", "job_title": "Data Scientist", "state": "MANUAL_HANDOFF_REQUIRED"},
    {"application_id": "c1", "company_name": "Gamma", "job_title": "Senior AI Engineer", "state": "VERIFIED"},
    {"application_id": "c2", "company_name": "Gamma", "job_title": "AI Engineer - Platform", "state": "VERIFIED"},
]


def _m(subject, text, domain="example.com", name=""):
    sig = _d(subject, text, domain, name)
    return match_rejection(sig, text_for_matching(subject, text), APPS)


def test_company_and_role_match_one_sent_application_is_automatic():
    r = _m("Your application to Machine Learning Engineer at Acme Analytics", "Unfortunately, we will not be moving forward with your application.")
    assert (r.decision, r.application_ids) == ("auto", ["a1"])


def test_no_role_named_asks_for_confirmation():
    r = _m("Your application at Acme Analytics", "Thank you for your interest in Acme Analytics. We regret to inform you that we will not proceed with your application.")
    assert r.decision == "confirm" and set(r.application_ids) == {"a1", "a2"}


def test_application_not_recorded_as_sent_is_never_automatic():
    r = _m("Update", "Thank you for your interest in Beta Labs. We decided to move forward with other candidates for the Data Scientist position.")
    assert (r.decision, r.application_ids) == ("confirm", ["b1"])


def test_role_tokens_must_really_match():
    r = _m("Regarding your application", "We regret to inform you that your application for the Senior AI Engineer position at Gamma was not selected.")
    assert (r.decision, r.application_ids) == ("auto", ["c1"])


def test_unknown_company_is_unmatched():
    r = _m("Your application to Python Developer at Unknown Corp", "Unfortunately we are not moving forward with your application.")
    assert r.decision == "unmatched" and r.application_ids == []


def test_gmail_query_is_fixed_wording_and_excludes_alerts():
    q = gmail_query(30)
    assert q.startswith("newer_than:30d") and '"regret to inform"' in q and "-from:jobalerts-noreply@linkedin.com" in q and "-in:sent" in q


def test_a_different_role_at_the_same_company_is_unmatched():
    r = _m("Your application to RAG Engineer at Gamma", "Unfortunately, we will not be moving forward with your application.")
    assert r.decision == "unmatched" and "no application for this role" in r.reason


def test_recommended_jobs_and_footer_are_never_used_for_matching():
    text = "\n".join(["Your update from Nowhere Corp", "Unfortunately, we will not be moving forward with your application.", "Top jobs looking for your skills",
                      "Machine Learning Engineer", "Acme Analytics Pvt Ltd", "This email was intended for Jane (Data Engineer at Acme Analytics)"])
    r = _m("Your application to Python Developer at Nowhere Corp", text)
    assert r.decision == "unmatched"
    assert "acme" not in text_for_matching("x", text)


def test_role_with_brackets_and_workday_subject():
    s = _d("Your application to NLP Engineer  (Remote) at Joblet-AI", "Unfortunately, we will not be moving forward with your application.", "linkedin.com")
    assert (s.company, s.role) == ("Joblet-AI", "NLP Engineer (Remote)")
    s = _d("Blue Yonder Careers - Data Scientist II - 254434", "Thank you for applying. We feel that there are other candidates whose backgrounds fit more closely with the requirements of the position.", "myworkday.com", "Workday Notification Do Not Reply")
    assert (s.company, s.role) == ("Blue Yonder", "Data Scientist II")


def test_linkedin_message_only_in_html_part():
    html = "<p>Thank you for your interest in the AI Prompt Engineer position at Insight Global.</p><p>Unfortunately, we will not be moving forward with your application.</p>"
    s = detect_rejection("Your application to AI Prompt Engineer at Insight Global", "Your update from Insight Global", html, sender_domain="linkedin.com")
    assert s is not None and s.company == "Insight Global"
