"""Jobs from an Instahyre "Job alert for saved search" e-mail in the candidate's own mailbox. Nothing here contacts Instahyre.

Real alert layout (checked against the candidate's own e-mails): every job is a card link to ``instahyre.com/job-<id>-<slug>/`` (wrapped in
Instahyre's click-tracking redirect, which is decoded, never followed), then the title, the company and the places, then an "Apply here"
link, a one-line company description and the skills as separate chips. The job links carry a personal sign-in token (``url_auth_token``):
only the clean job address is kept - the token and every other parameter are dropped. Alerts show no job description, so the job is
stored unscored (the company line and skills are not a job description) until its real description is imported. The listed skills
are kept (``listed_skills``) so they can be shown next to the candidate's own skills - information only, never a score.
"""
import re
from urllib.parse import unquote

from integrations.base_adapter import RawJob
from integrations.email.flat_stream import flat_items

_JOB = re.compile(r"https?://([a-z0-9.-]+)/(job-(\d{4,12})-[a-z0-9-]*)", re.I)
_NOISE = re.compile(r"^(?:apply here|apply|view job|actively hiring|new|urgent|hot job|featured|thanks,?|hi\b.*)$", re.I)
_REMOTE = re.compile(r"\b(?:work from home|remote|wfh)\b", re.I)
_FOOTER = re.compile(r"\b(?:the instahyre team|unsubscribe|manage (?:your )?(?:job )?alerts?|saved search settings|privacy policy)\b", re.I)


def instahyre_job_ref(href: str) -> tuple[str, str] | None:
    """(job id, clean job link) for an Instahyre job link - also inside the click-tracking redirect. None for anything else."""
    value = href or ""
    for _ in range(4):
        for m in _JOB.finditer(value):
            host = m.group(1).lower()
            if host == "instahyre.com" or host.endswith(".instahyre.com"):
                return m.group(3), f"https://www.instahyre.com/{m.group(2).lower()}/"
        decoded = unquote(value)
        if decoded == value:
            break
        value = decoded
    return None


def parse_instahyre_alert(html: str, text: str = "") -> tuple[list[RawJob], int]:
    items = flat_items(html)
    first: dict[str, int] = {}
    urls: dict[str, str] = {}
    for i, (kind, value) in enumerate(items):
        if kind == "link" and (ref := instahyre_job_ref(value)):
            first.setdefault(ref[0], i)
            urls[ref[0]] = ref[1]
    starts = sorted(first.values())
    jobs: list[RawJob] = []
    for jid, start in sorted(first.items(), key=lambda kv: kv[1]):
        end = next((s for s in starts if s > start), len(items))
        card: list[str] = []                           # title, company, places - the lines before the "Apply here" link
        after: list[str] = []                          # then a one-line company description and the skills, one chip per line
        seen_apply = False
        for kind, value in items[start + 1:end]:
            if kind == "link":
                seen_apply = seen_apply or bool(card)
                continue
            if _FOOTER.search(value):
                break
            if _NOISE.match(value):
                continue
            (after if seen_apply else card).append(value)
        # the company description is the first line after "Apply here" when it reads like a sentence; skill chips are short tags
        blurb = after[0] if after and (len(after[0]) > 45 or after[0].endswith(".") or len(after[0].split()) >= 5) else None
        skills = [x for x in after if x != blurb and 1 <= len(x) <= 40][:40]
        if not card:
            continue                                   # no readable title: not guessed
        title = card[0][:200]
        company = card[1][:150] if len(card) > 1 else None
        places = card[2] if len(card) > 2 else ""
        remote = bool(_REMOTE.search(places))
        locations = [] if remote else [p.strip() for p in places.split(",") if p.strip()][:10]
        jobs.append(RawJob(source="instahyre", provider="gmail_alert", source_job_id=jid, url=urls[jid], title=title, company=company,
                           location_text=locations, work_mode_text="Remote" if remote else None, description=None,
                           original={"via": "instahyre job-alert e-mail", "listed_skills": skills or None}))
    return jobs, 0
