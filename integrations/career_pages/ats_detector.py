"""ATS provider detection - deliberately separate from data extraction.

Looks only at a URL and (optionally) HTML the caller already has; it never makes a request. If the provider
cannot be identified with enough confidence the result is ``unsupported``.
"""
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

MIN_CONFIDENCE = 0.6
_TOKEN = re.compile(r"^[A-Za-z0-9_\-]{1,100}$")


@dataclass
class AtsDetection:
    provider: str  # greenhouse | lever | workday | generic | unsupported
    confidence: float
    board_token: str | None = None
    evidence: list[str] = field(default_factory=list)

    @property
    def supported(self) -> bool:
        return self.provider != "unsupported" and self.confidence >= MIN_CONFIDENCE


def safe_token(value: str | None) -> str | None:
    return value if value and _TOKEN.match(value) else None


def detect(url: str | None, html: str | None = None) -> AtsDetection:
    ev: list[str] = []
    host, path, query = "", "", {}
    if url:
        p = urlparse(url)
        host, path, query = (p.hostname or "").lower(), p.path, parse_qs(p.query)

    # ---- URL evidence (strong) ----
    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io", "boards.eu.greenhouse.io"):
        token = safe_token(path.strip("/").split("/")[0]) if path.strip("/") else None
        return AtsDetection("greenhouse", 0.95, token, [f"host {host}"])
    if host == "boards-api.greenhouse.io":
        m = re.match(r"^/v1/boards/([^/]+)", path)
        return AtsDetection("greenhouse", 0.95, safe_token(m.group(1)) if m else None, [f"host {host}"])
    if host in ("jobs.lever.co", "jobs.eu.lever.co"):
        return AtsDetection("lever", 0.95, safe_token(path.strip("/").split("/")[0]) if path.strip("/") else None, [f"host {host}"])
    if host in ("api.lever.co", "api.eu.lever.co"):
        m = re.match(r"^/v0/postings/([^/]+)", path)
        return AtsDetection("lever", 0.95, safe_token(m.group(1)) if m else None, [f"host {host}"])
    if host.endswith(".myworkdayjobs.com") or host.endswith(".myworkdaysite.com"):
        return AtsDetection("workday", 0.95, safe_token(host.split(".")[0]), [f"host {host}"])
    if "gh_jid" in query:
        ev.append("gh_jid query parameter")

    # ---- HTML evidence (weaker) ----
    if html:
        low = html[:300_000].lower()
        m = re.search(r"boards\.greenhouse\.io/embed/job_board(?:/js)?\?for=([a-z0-9_\-]+)", low)
        if m:
            return AtsDetection("greenhouse", 0.85, safe_token(m.group(1)), ev + ["greenhouse embed script"])
        if "grnhse" in low or "boards.greenhouse.io" in low or "job-boards.greenhouse.io" in low:
            ev.append("greenhouse markers in page")
            m = re.search(r"(?:job-)?boards\.greenhouse\.io/([a-z0-9_\-]+)", low)
            return AtsDetection("greenhouse", 0.75, safe_token(m.group(1)) if m and m.group(1) != "embed" else None, ev)
        m = re.search(r"jobs\.lever\.co/([a-z0-9_\-]+)", low)
        if m:
            return AtsDetection("lever", 0.8, safe_token(m.group(1)), ev + ["lever link in page"])
        if "myworkdayjobs.com" in low:
            return AtsDetection("workday", 0.75, None, ev + ["workday link in page"])
        if re.search(r'"@type"\s*:\s*"jobposting"', low):
            return AtsDetection("generic", 0.7, None, ev + ["schema.org JobPosting data"])
    if ev:  # only a weak URL hint, e.g. gh_jid with nothing else
        return AtsDetection("unsupported", 0.4, None, ev)
    return AtsDetection("unsupported", 0.0, None, ["no ATS signal found"])
