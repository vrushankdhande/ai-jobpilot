"""Jobs from a Glassdoor job-alert e-mail in the candidate's own mailbox. Nothing here contacts Glassdoor.

Real alert layout (checked against the candidate's own e-mails): each job is one link to ``glassdoor.com/partner/jobListing.htm?...`` whose
text is, line by line: company, rating ("4.0 ★", optional), title, location, salary estimate (optional), "Easy Apply" (optional), age ("4d").
Those links are one-time tracking links tied to the recipient (guid, alert and user ids) with no stable job number, so they are NEVER stored.
The job gets a stable id made from its company, title and location, and a Glassdoor SEARCH link for that title and company, built here
(never fetched) - the same idea as LinkedIn's "Find on LinkedIn". Alerts show no job description, so the job is stored unscored.
"""
import hashlib
import re
from urllib.parse import urlencode, urlparse

from integrations.base_adapter import RawJob
from integrations.email.linkedin_alerts import _Stream

_RATING = re.compile(r"^\d(?:\.\d)?\s*★?$")
_AGE = re.compile(r"^(\d{1,2})\s*([hd])\+?$", re.I)
_MONEY = re.compile(r"[₹$€£]|\best\.\)|\bper (?:year|month|hour)\b", re.I)
_NOISE = re.compile(r"^(?:easy apply|new|hiring|urgently hiring|responds quickly|top company)$", re.I)
_REMOTE = re.compile(r"\bremote\b", re.I)


def _is_job_link(href: str) -> bool:
    u = urlparse(href or "")
    host = (u.hostname or "").lower()
    return (host == "glassdoor.com" or host.endswith(".glassdoor.com") or re.fullmatch(r"(?:www\.)?glassdoor\.co\.[a-z]{2}", host) is not None) \
        and u.path.lower().endswith("/joblisting.htm")


def _search_link(title: str, company: str | None) -> str:
    return "https://www.glassdoor.co.in/Job/jobs.htm?" + urlencode({"sc.keyword": f"{title} {company or ''}".strip()[:150]})


def _age(text: str) -> str | None:
    m = _AGE.match(text.strip())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    return "today" if unit == "h" else f"{n} day{'s' if n != 1 else ''} ago"


def parse_glassdoor_alert(html: str, text: str = "") -> tuple[list[RawJob], int]:
    p = _Stream()
    p.feed(html or "")
    p.close()
    jobs: list[RawJob] = []
    seen: set[str] = set()
    for it in p.items:
        if it[0] != "link" or not _is_job_link(it[1]):
            continue
        lines = [s.strip() for s in it[2] if s.strip()]
        rest = [s for s in lines if not _RATING.match(s) and not _NOISE.match(s)]
        salary = next((s for s in rest if _MONEY.search(s)), None)
        posted = next((a for s in rest if (a := _age(s))), None)
        core = [s for s in rest if s != salary and not _age(s)]
        if len(core) < 2:
            continue                                    # needs at least company and title; nothing is guessed
        company, title = core[0][:150], core[1][:200]
        location = core[2][:150] if len(core) > 2 else None
        remote = bool(location and _REMOTE.search(location))
        jid = "gd-" + hashlib.sha1(f"{company}|{title}|{location or ''}".lower().encode()).hexdigest()[:16]
        if jid in seen:
            continue
        seen.add(jid)
        jobs.append(RawJob(source="glassdoor", provider="gmail_alert", source_job_id=jid, url=_search_link(title, company), title=title,
                           company=company, location_text=[] if remote or not location else [location], work_mode_text="Remote" if remote else None,
                           salary_text=salary, posted_raw=posted, description=None,
                           original={"via": "glassdoor job-alert e-mail", "link": "search (the e-mail's job links are one-time tracking links)"}))
    return jobs, 0
