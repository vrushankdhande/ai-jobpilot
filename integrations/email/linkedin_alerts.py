"""Jobs from a LinkedIn job-alert e-mail that the candidate received in their own mailbox ("Your job alert for ...").

Nothing here contacts LinkedIn: the e-mail's HTML is split into job links and the text around them. A job is recognised by its
LinkedIn job link (``linkedin.com/comm/jobs/view/<id>`` or ``/jobs/view/<id>``, also inside an encoded redirect - never followed).
Only the job title, company, location and work mode are kept: the names of the candidate's connections that these e-mails show
("Jane Doe · 5 connections"), "Actively recruiting" and similar lines are dropped and never stored. Unknown values stay empty.
"""
import html as html_lib
import re
from html.parser import HTMLParser
from urllib.parse import unquote

from integrations.base_adapter import RawJob

_JOB = re.compile(r"https?://([a-z0-9.-]+)/(?:comm/)?jobs/view/(?:[a-z0-9%-]*-)?(\d{6,15})(?=[/?#&\"'\s]|$)", re.I)
_FOOTER = re.compile(r"\b(?:see all jobs|unsubscribe|manage (?:your )?(?:job )?alerts?|job alert settings|you are receiving|this email was intended|"
                     r"learn why we included this|help center|privacy policy|user agreement|© ?\d{4} linkedin|linkedin corporation)\b", re.I)
_PEOPLE = re.compile(r"\b\d+\s+(?:connections?|school alumni|company alumni|alumni)\b|\bactively recruiting\b|\beasy apply\b|\bpromoted\b|"
                     r"\b(?:\d+\s+)?applicants?\b|\bbe an early applicant\b|\bviewed\b|\bnew\b$", re.I)
_WORK = re.compile(r"\((remote|hybrid|on-site|onsite)\)", re.I)
_SEP = " · "


def linkedin_job_ref(href: str) -> tuple[str, str] | None:
    """(job id, clean job link) for a LinkedIn job link, also one URL-encoded inside a redirect. None for anything else (look-alikes too)."""
    value = html_lib.unescape(href or "")
    for _ in range(4):
        for m in _JOB.finditer(value):
            host = m.group(1).lower()
            if host == "linkedin.com" or host.endswith(".linkedin.com"):
                return m.group(2), f"https://www.linkedin.com/jobs/view/{m.group(2)}/"
        decoded = unquote(value)
        if decoded == value:
            break
        value = decoded
    return None


def _company_location(line: str) -> tuple[str | None, str | None, str | None]:
    """"Solifi · Bengaluru (Hybrid)" -> (company, location, work mode)."""
    parts = [p.strip() for p in line.split("·") if p.strip()]
    if len(parts) < 2:
        return None, None, None
    company, location = parts[0], parts[1]
    m = _WORK.search(location)
    mode = {"on-site": "On-site", "onsite": "On-site"}.get(m.group(1).lower(), m.group(1).capitalize()) if m else None
    return company[:150], location[:150], mode


_BLOCK = {"p", "div", "br", "tr", "td", "th", "li", "table", "h1", "h2", "h3", "h4", "h5", "h6", "span", "section"}
_SKIP = {"script", "style", "head", "title"}


class _Stream(HTMLParser):
    """The e-mail as text lines and links in reading order. A link keeps its text as separate lines (a whole job card is often ONE link:
    title, then "Company · Location"), so the parts are not run together."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items: list[tuple] = []           # ("text", line) | ("link", href, [lines])
        self._line: list[str] = []
        self._href: str | None = None
        self._segs: list[str] = []
        self._cur: list[str] = []
        self._skip = 0

    @staticmethod
    def _join(parts: list[str]) -> str:
        return re.sub(r"\s+", " ", "".join(parts)).strip()

    def _flush(self):
        if self._href is not None:
            t = self._join(self._cur)
            if t:
                self._segs.append(t)
            self._cur = []
        else:
            t = self._join(self._line)
            if t:
                self.items.append(("text", t))
            self._line = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip += 1
        elif tag == "a":
            self._flush()
            self._href, self._segs, self._cur = dict(attrs).get("href") or "", [], []
        elif tag in _BLOCK:
            self._flush()

    def handle_endtag(self, tag):
        if tag in _SKIP and self._skip:
            self._skip -= 1
        elif tag == "a" and self._href is not None:
            self._flush()
            self.items.append(("link", self._href, self._segs))
            self._href = None
        elif tag in _BLOCK:
            self._flush()

    def handle_data(self, data):
        if not self._skip:
            (self._cur if self._href is not None else self._line).append(data)

    def close(self):
        super().close()
        if self._href is not None:
            self._flush()
            self.items.append(("link", self._href, self._segs))
            self._href = None
        self._flush()


def parse_linkedin_alert(html: str, text: str = "") -> tuple[list[RawJob], int]:
    """Jobs found in one LinkedIn job-alert e-mail. Second value: links that looked like jobs but could not be read (always 0 here)."""
    p = _Stream()
    p.feed(html or "")
    p.close()
    items = p.items

    first: dict[str, int] = {}
    urls: dict[str, str] = {}
    for i, it in enumerate(items):
        if it[0] == "link" and (ref := linkedin_job_ref(it[1])):
            first.setdefault(ref[0], i)
            urls[ref[0]] = ref[1]

    starts = sorted(first.values())
    jobs: list[RawJob] = []
    for jid, start in sorted(first.items(), key=lambda kv: kv[1]):
        end = next((s for s in starts if s > start), len(items))
        lines: list[tuple[str, bool]] = []          # (line, came from this job's own link)
        for it in items[start:end]:
            if it[0] == "link":
                own = bool(linkedin_job_ref(it[1]))
                lines += [(seg, own) for seg in it[2]]
            else:
                lines.append((it[1], False))
        company = location = mode = None
        kept: list[tuple[str, bool]] = []
        for line, own in lines:
            if _FOOTER.search(line):
                break
            if _PEOPLE.search(line):
                continue                             # connections, "Actively recruiting", applicant counts: never kept
            if company is None and "·" in line:
                company, location, mode = _company_location(line)
                continue
            kept.append((line, own))
        # the title: text of the job's own link that is not the company name (a logo link may carry the company name)
        title = next((ln for ln, own in kept if own and 3 <= len(ln) <= 150 and (not company or ln.lower() != company.lower())), None)
        if not title:
            continue                                 # a job without a readable title is not guessed
        jobs.append(RawJob(source="linkedin", provider="gmail_alert", source_job_id=jid, url=urls[jid], title=title[:200], company=company,
                           location_text=[location] if location else [], work_mode_text=mode, description=None,
                           original={"via": "linkedin job-alert e-mail"}))
    return jobs, 0
