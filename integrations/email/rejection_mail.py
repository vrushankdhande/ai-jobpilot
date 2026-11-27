"""Recognises a reply to one of the candidate's applications that says "not selected", and reads which company and role it is about.

Rule-based and deterministic. Nothing is sent anywhere, no link is opened, and the e-mail body is used in memory only: the result keeps the
company, the role, the sender's domain, the date and the short rejection phrase that was found - never the text of the e-mail.
Values that cannot be read stay None (never guessed); a line containing an e-mail address or phone number is never used.
"""
import html as html_lib
import re
from dataclasses import dataclass, field
from datetime import datetime

# Gmail is asked only for messages containing one of these phrases (a pre-filter; the patterns below decide)
GMAIL_PHRASES = ("not moving forward", "not be moving forward", "not to move forward", "move forward with other", "regret to inform", "not been selected",
                 "not been shortlisted", "not shortlisted", "unfortunately", "other candidates", "position has been filled", "unable to offer",
                 "not to proceed", "not proceed with your application", "unsuccessful")
# senders that only ever send job alerts / marketing - never a reply to an application
ALERT_ONLY = ("jobalerts-noreply@linkedin.com",)

_REJECT = [re.compile(p, re.I) for p in (
    r"\b(?:not|won't|will not|would not|wouldn't) (?:be )?(?:moving|move|going) (?:forward|ahead)\b",
    r"\bdecided (?:not to (?:proceed|move forward|pursue|progress|continue)|to (?:move forward|proceed|go ahead|continue) with (?:other|another|a different|more) (?:candidates?|applicants?|profiles?))",
    r"\b(?:move|moving) forward with (?:other|another|a different) (?:candidates?|applicants?)\b",
    r"\bregret to (?:inform|let you know|tell you)\b",
    r"\b(?:have|has|were|was) not been (?:selected|shortlisted|successful|chosen)\b",
    r"\b(?:you|your (?:application|profile|candidature|candidacy)) (?:was|were|is|has been)? ?not (?:selected|shortlisted|successful|progressed)\b",
    r"\bnot (?:been )?shortlisted\b",
    r"\bposition (?:has|have) (?:now )?been filled\b",
    r"\bunable to offer you\b",
    r"\b(?:pursue|pursuing|proceed with|proceeding with) other candidates\b",
    r"\bnot (?:to )?(?:proceed|progress)(?:ing)? (?:further )?with your (?:application|candidacy|candidature)\b",
    r"\byour application (?:was|has been) unsuccessful\b|\bunsuccessful (?:on this occasion|this time)\b",
    r"\bapplication status\s*[:\-]\s*(?:rejected|not selected|not shortlisted)\b",
    r"\bother (?:candidates|applicants) (?:whose|who) (?:backgrounds?|experience|skills|qualifications|profiles?)\b[^.\n]{0,40}\b(?:more closely|better|more)\b",
)]
# the mail must be about the candidate's application, not e.g. a newsletter saying "unfortunately"
_CONTEXT = re.compile(r"\b(?:your application|your candidacy|your candidature|your profile|applying|applied|application (?:for|to)|your interest in|interviewing with us|"
                      r"(?:this|the) (?:position|role|opening|vacancy|requisition))\b", re.I)
# the Gmail pre-filter also needs one of these words, so bank statements or news saying "unfortunately" are not even returned
GMAIL_CONTEXT = ("your application", "applying", "applied", "your interest", "candidacy", "candidature", "position", "role")
_PERSONAL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|(?<!\d)(?:\+?\d[\s-]?){10,13}(?!\d)")

_STOP_TAIL = r"(?=\s*(?:[.,;:!?\n()|]|\s-\s|\s(?:and|has|have|had|we|is|was|were|which|that|but|after|in|on|for|this|team)\b|$))"
_ROLE_TXT = r"[\"“'‘]?(?P<role>[A-Za-z0-9](?:[^\n\"”’.,;:!?|]|\.(?=\S)){1,110}?)[\"”'’]?"     # "Python Developer (Cloud + React.js)"
_CO_TXT = r"(?P<company>[A-Z0-9][\w&@.'’\- ]{0,60}?)"
_BOTH = [re.compile(p, re.M) for p in (
    # Workday subject: "Blue Yonder Careers - Data Scientist II - 254434"
    rf"^{_CO_TXT}\s+(?i:careers?|jobs)\s+[-–]\s+{_ROLE_TXT}\s+[-–]\s+[A-Za-z_-]*\d{{3,}}\s*$",
    # "Your application to Data Scientist at Acme" (LinkedIn), "application for the ML Engineer position at Acme"
    rf"(?i:(?:application|applying|applied|candidacy|candidature|interest)\s+(?:for|to|in)\s+(?:the\s+)?(?:position\s+of\s+|role\s+of\s+|post\s+of\s+)?){_ROLE_TXT}(?i:\s+(?:position|role|opening|job|post|vacancy))?\s+(?i:at|with)\s+{_CO_TXT}{_STOP_TAIL}",
    rf"(?i:the\s+){_ROLE_TXT}(?i:\s+(?:position|role|opening|vacancy))\s+(?i:at|with)\s+{_CO_TXT}{_STOP_TAIL}",
)]
_ROLE_ONLY = [re.compile(p, re.M) for p in (
    rf"(?i:(?:application|applying|applied|candidacy|candidature|interest)\s+(?:for|to|in)\s+the\s+(?:position\s+of\s+|role\s+of\s+)?){_ROLE_TXT}(?i:\s+(?:position|role|opening|vacancy|job))",
    rf"(?i:(?:position|role|post|job title|designation)\s*(?:of|:|-|–)\s*){_ROLE_TXT}{_STOP_TAIL}",
    rf"(?i:the\s+){_ROLE_TXT}(?i:\s+(?:position|role|opening|vacancy))\b",
)]
_CO_ONLY = [re.compile(p, re.M) for p in (
    rf"(?i:thank(?:s| you) for (?:your interest in|applying (?:to|at|with)|considering|taking the time to apply (?:to|at|for a (?:role|position) at))\s+(?:a (?:role|position|career) (?:at|with)\s+)?){_CO_TXT}{_STOP_TAIL}",
    rf"(?i:(?:interest in|applying to|applying with|opportunity (?:at|with)|career(?:s| opportunities)? (?:at|with)|role at|position at|joining)\s+){_CO_TXT}{_STOP_TAIL}",
    rf"(?i:the\s+){_CO_TXT}(?i:\s+(?:recruiting|recruitment|talent acquisition|talent|hiring|people|hr)\s+team)\b",
)]
_SENDER_SUFFIX = re.compile(r"\s*(?:[-|@,]\s*)?(?:\bvia\b.*|recruiting|recruitment|recruiter|careers?|jobs|talent(?: acquisition)?(?: team)?|hiring(?: team)?|hr(?: team| portal)?|portal|"
                            r"people(?: team)?|team|no-?reply|notifications?|do not reply)\s*$", re.I)
_GENERIC_NAMES = {"", "noreply", "no-reply", "no reply", "linkedin", "naukri", "naukri.com", "indeed", "instahyre", "glassdoor", "workday", "greenhouse", "lever",
                  "smartrecruiters", "ashby", "icims", "taleo", "successfactors", "recruitee", "workable", "jobvite", "notifications", "do not reply",
                  "talent acquisition", "recruiting", "hiring team", "careers", "hr", "team", "recruitment", "the team", "we", "us", "our team"}
_GENERIC_DOMAINS = ("gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "yahoo.com", "yahoo.co.in", "live.com", "icloud.com", "rediffmail.com",
                    "linkedin.com", "naukri.com", "indeed.com", "instahyre.com", "glassdoor.com", "greenhouse.io", "lever.co", "myworkday.com",
                    "workday.com", "smartrecruiters.com", "ashbyhq.com", "icims.com", "taleo.net", "successfactors.com", "recruitee.com",
                    "workable.com", "jobvite.com", "freshteam.com", "zohorecruit.com", "keka.com", "darwinbox.in", "hirist.com", "sendgrid.net",
                    "mailgun.org", "amazonses.com")
_BAD_ROLE = re.compile(r"^(?:this|that|the|a|an|our|your|my|it|position|role|job|opportunity|application|team|company|time|interest|us|you|which|other|current|open|"
                       r"above|said|mentioned|applied|same|following|available|advertised|specific)$"
                       r"|^(?:time|interest|consideration|patience|effort|requirements?)\b|\b(?:the|of|a|an|to|for|and|with|in|at)$", re.I)
# where the message ends: a job site's recommendations ("Top jobs looking for your skills") and footers name OTHER companies and the
# candidate's own name/current job - nothing after these lines is used
_END = re.compile(r"^(?:top jobs|jobs (?:you may|similar|for you|recommended|looking for|based on)|similar jobs|recommended (?:jobs|for you)|more jobs|see more jobs|"
                  r"people also (?:viewed|applied)|explore (?:more|similar)|you may also|other jobs|this (?:e-?mail|message) was (?:intended|sent) (?:for|to)|"
                  r"you are receiving|you received this|unsubscribe|learn why we included|get the (?:new )?\w+ app)", re.I)


@dataclass
class RejectionSignal:
    company: str | None
    role: str | None
    phrase: str                                 # the short rejection wording that was found (<= 80 chars)
    sender_domain: str
    company_from_sender: str | None = None      # e.g. "Acme" from "Acme Careers" or careers.acme.com - a matching hint only
    received_at: datetime | None = None
    notes: list[str] = field(default_factory=list)


def html_to_text(html: str) -> str:
    s = re.sub(r"(?is)<(script|style|head|title)\b.*?</\1>", " ", html or "")
    s = re.sub(r"(?i)<br\s*/?>|</(?:p|div|tr|li|h[1-6]|table|td)>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    return html_lib.unescape(s)


def _clean_lines(text: str) -> str:
    lines = []
    for line in (text or "").splitlines():
        line = re.sub(r"[ \t\u00a0]+", " ", line).strip()
        if line.startswith(">") or re.match(r"^on .{4,80} wrote:$", line, re.I) or _END.match(line):   # quoted earlier mails, recommendations, footers
            break
        if line and not _PERSONAL.search(line):
            lines.append(line)
    return "\n".join(lines)


def _body(text: str, html: str) -> str:
    """Readable body from the HTML part and the text part together: some senders (LinkedIn) put the message only in the HTML part."""
    parts = [_clean_lines(html_to_text(html)) if html else "", _clean_lines(text)]
    return "\n".join(p for p in parts if p)


def _tidy(value: str | None, *, role: bool = False) -> str | None:
    if not value:
        return None
    v = re.sub(r"\s+", " ", value).strip(" -–:'\"“”‘’")
    v = re.sub(r"^(?:the|a|an)\s+", "", v, flags=re.I)
    v = re.sub(r"\s+(?:position|role|opening|vacancy|job)$", "", v, flags=re.I) if role else v
    v = re.sub(r"\s*\((?:m/f/d|f/m/d|all genders)\)$", "", v, flags=re.I)
    if not v or len(v) < 2 or len(v) > 90 or _PERSONAL.search(v) or (role and _BAD_ROLE.search(v)) or v.lower() in _GENERIC_NAMES:
        return None
    return v


def company_from_sender(name: str, domain: str) -> str | None:
    """A company hint from the sender: "Acme Careers" -> "Acme"; careers.acme.co.in -> "acme". None for job sites, ATS and webmail."""
    n = (name or "").strip()
    for _ in range(3):
        n = _SENDER_SUFFIX.sub("", n).strip(" -|,@")
    if n and n.lower() not in _GENERIC_NAMES and len(n) <= 60 and not re.search(r"\d{3,}|@", n):
        return n
    d = (domain or "").lower()
    if d and not any(d == g or d.endswith("." + g) for g in _GENERIC_DOMAINS):
        parts = [p for p in d.split(".") if p not in ("com", "co", "in", "io", "ai", "org", "net", "careers", "jobs", "mail", "email", "hr", "talent", "recruiting", "www", "e", "m")]
        if parts:
            return parts[-1]
    return None


def detect_rejection(subject: str, text: str, html: str = "", *, sender_domain: str = "", sender_name: str = "", received_at: datetime | None = None) -> RejectionSignal | None:
    """A RejectionSignal when the mail says the candidate's application did not go through, else None."""
    body = _body(text, html)
    blob = f"{subject or ''}\n{body}"
    hit = next((m for rx in _REJECT for m in [rx.search(blob)] if m), None)
    if hit is None or not _CONTEXT.search(blob):
        return None
    company = role = None
    for source in (subject or "", body):
        for rx in _BOTH:
            if company is None and (m := rx.search(source)):
                role, company = _tidy(m.group("role"), role=True), _tidy(m.group("company"))
                if not company:
                    role = None
        if role is None:
            role = next((r for rx in _ROLE_ONLY for m in [rx.search(source)] if m for r in [_tidy(m.group("role"), role=True)] if r), None)
        if company is None:
            company = next((c for rx in _CO_ONLY for m in [rx.search(source)] if m for c in [_tidy(m.group("company"))] if c), None)
    return RejectionSignal(company=company, role=role, phrase=re.sub(r"\s+", " ", hit.group(0))[:80], sender_domain=(sender_domain or "").lower(),
                           company_from_sender=company_from_sender(sender_name, sender_domain), received_at=received_at)


def text_for_matching(subject: str, text: str, html: str = "") -> str:
    """Subject + the start of the readable body (personal lines, quotes, recommendations and footers removed), lower-cased - used in memory
    to look for an application's company/title."""
    return f"{subject or ''}\n{_body(text, html)[:1500]}".lower()
