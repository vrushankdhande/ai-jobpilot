"""Jobs from a Naukri job-alert e-mail that the candidate received in their own mailbox.

Nothing here contacts Naukri: the e-mail's HTML is split into job links and the text around them. A job is recognised by its
Naukri job link (``naukri.com/job-listings-…-<id>``), also when the e-mail wraps it in a click-tracking redirect (the target is
decoded from the link itself - the redirect is never followed). Links that hide the job entirely are skipped and counted.
Unknown values stay empty; nothing is guessed.
"""
import html as html_lib
import re
from html.parser import HTMLParser
from urllib.parse import unquote

from integrations.base_adapter import RawJob

_ABS = re.compile(r"https?://([a-z0-9.-]+)/(job-listings-[a-z0-9-]*?-(\d{9,15}))(?=[/?#&\"'\s]|$)", re.I)
_REL = re.compile(r"^/(job-listings-[a-z0-9-]*?-(\d{9,15}))(?=[/?#]|$)", re.I)
_BLOCK = {"p", "div", "br", "tr", "td", "th", "li", "ul", "ol", "table", "h1", "h2", "h3", "h4", "h5", "h6", "section"}
_SKIP = {"script", "style", "head", "title"}

_EXP = re.compile(r"^(?:exp(?:erience)?\s*[:\-]?\s*)?(\d{1,2})\s*(?:-|–|to)\s*(\d{1,2})\s*(?:yrs?|years?)\.?$|^(?:exp(?:erience)?\s*[:\-]?\s*)?(\d{1,2})\+?\s*(?:yrs?|years?)\.?$|^fresher$", re.I)
_SALARY = re.compile(r"^(?:not disclosed|₹.*|rs\.?\s.*|.*\b(?:lacs?|lakhs?|lpa|crore|cr)\b.*(?:p\.?a\.?)?)$", re.I)
_POSTED = re.compile(r"^(?:posted\s*:?\s*)?(?:just now|today|few hours ago|\d{1,2}\s*hours?\s*ago|\d{1,2}\+?\s*days?\s*ago|yesterday)$", re.I)
_NOISE = re.compile(r"^(?:apply|apply now|view job|view details|view & apply|save|saved|know more|see job|\||•|·|-|\d(?:\.\d)?|[\d,.]+k?\s*reviews?|new|hot job|"
                    r"recommended|easy apply|early applicant)$", re.I)
_BAD_TITLE = re.compile(r"^(?:apply|apply now|view|view job|view details|view & apply|know more|see job|see all|view all.*|similar jobs|https?://.*|www\..*)$", re.I)
_FOOTER = re.compile(r"\b(?:unsubscribe|manage (?:your )?(?:job )?alerts?|view all (?:jobs|matching jobs)|modify (?:this )?alert|privacy policy|"
                     r"you are receiving this|download (?:the )?(?:naukri )?app|terms (?:and|&) conditions)\b", re.I)
_WORK = re.compile(r"\b(?:remote|hybrid|work from home|wfh|on-?site)\b", re.I)


def naukri_job_ref(href: str) -> tuple[str, str] | None:
    """(job id, clean job link) for a Naukri job link - also one wrapped in a click-tracking URL. None otherwise."""
    value = html_lib.unescape(href or "")
    for _ in range(4):                          # tracking links carry the target URL-encoded, sometimes twice
        for m in _ABS.finditer(value):
            host = m.group(1).lower()
            if host == "naukri.com" or host.endswith(".naukri.com"):      # never a look-alike such as naukri.com.evil.example
                return m.group(3), f"https://www.naukri.com/{m.group(2).lower()}"
        m = _REL.match(value)
        if m:
            return m.group(2), f"https://www.naukri.com/{m.group(1).lower()}"
        decoded = unquote(value)
        if decoded == value:
            break
        value = decoded
    return None


class _Tokens(HTMLParser):
    """The e-mail as a sequence of text lines and links, in reading order."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items: list[tuple] = []           # ("text", line) | ("link", href, anchor_text)
        self._line: list[str] = []
        self._href: str | None = None
        self._anchor: list[str] = []
        self._skip = 0

    def _flush(self):
        line = re.sub(r"\s+", " ", "".join(self._line)).strip()
        if line:
            self.items.append(("text", line))
        self._line = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip += 1
        elif tag == "a":
            self._flush()
            self._href, self._anchor = dict(attrs).get("href") or "", []
        elif tag in _BLOCK:
            self._flush()

    def handle_endtag(self, tag):
        if tag in _SKIP and self._skip:
            self._skip -= 1
        elif tag == "a" and self._href is not None:
            self.items.append(("link", self._href, re.sub(r"\s+", " ", "".join(self._anchor)).strip()))
            self._href = None
        elif tag in _BLOCK:
            self._flush()

    def handle_data(self, data):
        if self._skip:
            return
        (self._anchor if self._href is not None else self._line).append(data)

    def close(self):
        super().close()
        self._flush()


def _is_place(line: str) -> bool:
    from engines.discovery.discovery_engine import _COUNTRY_PLACES   # lookup data; imported late (engines.discovery imports integrations)
    names = {n for _, (_, ns) in _COUNTRY_PLACES.items() for n in ns}
    parts = [p.strip().lower() for p in re.split(r"[,/|]|\s-\s", re.sub(r"\(.*?\)", "", line)) if p.strip()]
    return bool(parts) and all(p in names or _WORK.fullmatch(p) or p in ("india", "anywhere in india") for p in parts)


def _title_from_slug(slug: str) -> str:
    words = re.sub(r"-\d+-to-\d+-years?-\d+$|-\d{9,15}$", "", slug.removeprefix("job-listings-")).split("-")
    return " ".join(w.upper() if len(w) <= 2 else w.capitalize() for w in words if w)


def parse_naukri_alert(html: str, text: str = "") -> tuple[list[RawJob], int]:
    """Jobs found in one alert e-mail, and how many links looked like job links but hid the job (not followed, not used)."""
    p = _Tokens()
    p.feed(html or "")
    p.close()
    items = p.items
    if not items and text:                      # plain-text alert: one URL per job, details on the lines around it
        for line in text.splitlines():
            for url in re.findall(r"https?://\S+", line):
                items.append(("link", url, ""))
            rest = re.sub(r"https?://\S+", "", line).strip()
            if rest:
                items.append(("text", rest))

    first: dict[str, int] = {}
    titles: dict[str, list[str]] = {}
    urls: dict[str, str] = {}
    hidden = 0
    for i, it in enumerate(items):
        if it[0] != "link":
            continue
        ref = naukri_job_ref(it[1])
        if not ref:
            if re.search(r"naukri", it[1] or "", re.I) and re.search(r"\b(?:job|apply|jd)\b", it[2] or "", re.I):
                hidden += 1
            continue
        jid, url = ref
        first.setdefault(jid, i)
        urls[jid] = url
        if it[2] and not _BAD_TITLE.match(it[2]) and 3 <= len(it[2]) <= 150:
            titles.setdefault(jid, []).append(it[2])

    starts = sorted(first.values())
    jobs: list[RawJob] = []
    for jid, start in sorted(first.items(), key=lambda kv: kv[1]):
        end = next((s for s in starts if s > start), len(items))
        title = (titles.get(jid) or [""])[0] or _title_from_slug(urls[jid].rsplit("/", 1)[-1])
        lines: list[str] = []
        for it in items[start + 1:end]:
            line = it[1] if it[0] == "text" else it[2]
            if it[0] == "link" and naukri_job_ref(it[1]):
                continue
            if not line:
                continue
            if _FOOTER.search(line):
                break
            lines.append(line)
        company = experience = salary = posted = None
        locations: list[str] = []
        details: list[str] = []
        for line in lines:
            if line == title or _NOISE.match(line):
                continue
            if experience is None and _EXP.match(line):
                experience = line
            elif salary is None and _SALARY.match(line):
                salary = line
            elif posted is None and _POSTED.match(line):
                posted = re.sub(r"^posted\s*:?\s*", "", line, flags=re.I)
            elif not locations and _is_place(line):
                locations = [line]
            elif company is None and len(line) <= 100 and len(line.split()) <= 10 and "@" not in line:
                company = line
            else:
                details.append(line)
        description = "\n".join(([f"Experience: {experience}"] if experience else []) + details) or None
        jobs.append(RawJob(source="naukri", provider="gmail_alert", source_job_id=jid, url=urls[jid], title=title, company=company,
                           description=description, location_text=locations, salary_text=salary if salary and not re.match(r"not disclosed", salary, re.I) else None,
                           work_mode_text=next((m.group(0) for loc in locations for m in [_WORK.search(loc)] if m), None), posted_raw=posted,
                           original={"via": "naukri job-alert e-mail", "experience": experience}))
    return jobs, hidden
