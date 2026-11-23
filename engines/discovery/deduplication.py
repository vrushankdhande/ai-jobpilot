"""Duplicate detection: exact identifiers first, fuzzy matching only when needed, never auto-merge when unsure.

Levels
  exact_source_id   same source + source job id (+ same company: ids from different boards can collide)
  canonical_url     same canonical URL (tracking parameters removed)
  cross_platform    same company + same title + compatible location + near-identical description  -> MERGE
  possible          similar but not certain                                                         -> FLAG for review
Different vacancies at one company (other city, other requisition id, different description) are never merged.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from database.models.job import JobRecord

TRACKING_PARAMS = {
    "gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "igshid", "_hsenc", "_hsmi", "ref", "refid", "referrer", "trk",
    "trackingid", "tracking", "gh_src", "lever-source", "lever-origin", "lever-via", "source", "src", "campaign",
    "cmpid", "trkinfo", "origin", "sid", "from", "si", "utm_id",
}


# ------------------------------------------------------------------------------------ URLs
def canonical_url(url: str | None) -> str | None:
    """Stable form of a job URL. Only known tracking parameters are removed; unknown ones are kept (safe)."""
    if not url:
        return None
    try:
        p = urlparse(url.strip())
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or not p.hostname:
        return None
    host = p.hostname.lower()
    host = host[4:] if host.startswith("www.") else host
    path = re.sub(r"/{2,}", "/", p.path)
    params = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=False)
              if k.lower() not in TRACKING_PARAMS and not k.lower().startswith("utm_")]

    # platform specific identity
    if host.endswith("linkedin.com"):
        m = re.search(r"/jobs/view/(?:[\w\-%]*?-)?(\d{6,})", path)
        jid = m.group(1) if m else dict(params).get("currentJobId")
        if jid:
            return f"https://linkedin.com/jobs/view/{jid}"
    if host.endswith("indeed.com"):
        jk = dict(params).get("jk") or dict(params).get("vjk")
        if jk:
            return f"https://{host}/viewjob?jk={jk}"
    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io", "boards.eu.greenhouse.io"):
        m = re.match(r"^/([^/]+)/jobs/(\d+)", path)
        if m:
            return f"https://boards.greenhouse.io/{m.group(1)}/jobs/{m.group(2)}"
    if host in ("jobs.lever.co", "jobs.eu.lever.co"):
        m = re.match(r"^/([^/]+)/([0-9a-fA-F\-]{20,})", path)
        if m:
            return f"https://jobs.lever.co/{m.group(1)}/{m.group(2).lower()}"
    if host.endswith("myworkdayjobs.com") or host.endswith("naukri.com"):
        params = []  # their identity is in the path

    if len(path) > 1:
        path = path.rstrip("/")
    port = f":{p.port}" if p.port and p.port not in (80, 443) else ""
    query = urlencode(sorted(params))
    return urlunparse(("https", f"{host}{port}", path or "/", "", query, ""))


# --------------------------------------------------------------------------------------- keys
def _fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


_ABBREV = {"sr": "senior", "jr": "junior", "eng": "engineer", "engg": "engineer", "sw": "software", "dev": "developer",
           "ml": "machine learning", "mgr": "manager"}
_LEGAL = {"inc", "incorporated", "llc", "ltd", "limited", "pvt", "private", "corp", "corporation", "co", "gmbh", "plc",
          "llp", "ag", "bv", "sa", "pte"}
_CITY_ALIAS = {"bangalore": "bengaluru", "bombay": "mumbai", "gurgaon": "gurugram", "new delhi": "delhi", "madras": "chennai",
               "calcutta": "kolkata", "poona": "pune"}


def title_key(title: str | None) -> str:
    t = _fold(title or "").lower().replace("&", " and ")
    t = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", t)                       # (m/f/d), [Remote], (Req #123)
    t = re.sub(r"[-–—|,:]\s*(?:req(?:uisition)?|r|jr|id)?[-_ #]*\d{3,}\b.*$", " ", t)  # "- R12345" tails
    toks = [_ABBREV.get(w, w) for w in re.findall(r"[a-z0-9+#]+", t)]
    return " ".join(" ".join(toks).split())


def company_key(name: str | None) -> str:
    toks = re.findall(r"[a-z0-9]+", _fold(name or "").lower())
    if toks and toks[0] == "the":
        toks = toks[1:]
    while toks and toks[-1] in _LEGAL:
        toks = toks[:-1]
    return " ".join(toks)


def location_keys(locations: list[str]) -> list[str]:
    out = []
    for loc in locations:
        city = re.split(r"[,(]", _fold(loc).lower())[0].strip()
        city = _CITY_ALIAS.get(city, city)
        if city and city not in out:
            out.append(city)
    return out


def requisition_ids(text: str | None) -> set[str]:
    return {m.upper() for m in re.findall(r"(?:requisition|req|job|reference|ref)\s*(?:id|no\.?|number|#)?\s*[:#]?\s*([A-Z0-9][A-Z0-9\-]{3,})",
                                          text or "", flags=re.I) if re.search(r"\d", m)}


# -------------------------------------------------------------------------------- similarity
def _tokens(text: str | None) -> list[str]:
    return re.findall(r"[a-z0-9+#]+", _fold(text or "").lower())


def description_similarity(a: str | None, b: str | None) -> float | None:
    """Jaccard similarity of 3-word shingles (token Jaccard for very short texts). None if a text is missing."""
    ta, tb = _tokens(a), _tokens(b)
    if len(ta) < 8 or len(tb) < 8:
        return None
    sa = {tuple(ta[i:i + 3]) for i in range(len(ta) - 2)}
    sb = {tuple(tb[i:i + 3]) for i in range(len(tb) - 2)}
    return len(sa & sb) / len(sa | sb)


def title_similarity(a: str, b: str) -> float:
    sa, sb = set(a.split()), set(b.split())
    return len(sa & sb) / len(sa | sb) if sa and sb else 0.0


# -------------------------------------------------------------------------------- decisions
@dataclass
class DedupConfig:
    merge_similarity: float = 0.85          # description similarity needed to auto-merge a cross-platform duplicate
    merge_similarity_unknown_location: float = 0.92
    review_similarity: float = 0.5          # between this and merge_similarity -> flag, never merge
    title_fuzzy: float = 0.8                # token overlap for "same title, different wording" (flag only)


@dataclass
class DedupDecision:
    action: str  # insert | update | unchanged | merge | flag
    match_job_id: str | None = None
    confidence: float = 0.0
    level: str = "none"
    reasons: list[str] = field(default_factory=list)


_RANK = {"insert": 0, "flag": 1, "merge": 2, "update": 3, "unchanged": 3}


def _location_overlap(new: JobRecord, cand: dict) -> str:
    """'match' | 'conflict' | 'unknown'"""
    a, b = set(new.location_keys), set(cand.get("location_keys") or [])
    if new.work_mode == "remote" and cand.get("work_mode") == "remote":
        return "match"
    if not a or not b:
        return "unknown"
    return "match" if a & b else "conflict"


def decide(new: JobRecord, candidates: list[dict], cfg: DedupConfig | None = None) -> DedupDecision:
    cfg = cfg or DedupConfig()
    best = DedupDecision("insert", reasons=["no matching vacancy found"])
    ref = new.source_refs[0] if new.source_refs else None

    def consider(d: DedupDecision):
        nonlocal best
        if (_RANK[d.action], d.confidence) > (_RANK[best.action], best.confidence):
            best = d

    for cand in candidates:
        cid = cand["job_id"]
        refs = cand.get("source_refs") or []
        same_company = bool(new.company_key) and new.company_key == cand.get("company_key")

        # 1. exact identifiers ---------------------------------------------------------------
        if ref:
            for r in refs:
                id_match = (ref.source_job_id and r.get("source_job_id") == ref.source_job_id and r.get("source") == ref.source
                            and (same_company or not new.company_key or not cand.get("company_key")))
                url_match = ref.canonical_url and r.get("canonical_url") == ref.canonical_url
                if id_match or url_match:
                    level = "exact_source_id" if id_match else "canonical_url"
                    previous = r.get("content_hash") or cand.get("content_hash")  # per-source hash when stored
                    changed = previous != (new.content_hash or new.compute_content_hash())
                    consider(DedupDecision("update" if changed else "unchanged", cid, 1.0, level,
                                           [f"same {'source id' if id_match else 'canonical URL'}" + (" (content changed)" if changed else "")]))
                    break
        if not same_company:
            continue

        # 2. same company: is it the same vacancy on another platform? ------------------------------
        loc = _location_overlap(new, cand)
        if loc == "conflict":
            continue  # same company, same-ish title, different place => a different vacancy
        ids_new, ids_old = requisition_ids(new.job_description), requisition_ids(cand.get("job_description"))
        if ids_new and ids_old and not (ids_new & ids_old):
            continue  # different requisition ids => different vacancy
        sim = description_similarity(new.job_description, cand.get("job_description"))
        same_title = bool(new.title_key) and new.title_key == cand.get("title_key")
        fuzzy_title = (not same_title and title_similarity(new.title_key, cand.get("title_key", "")) >= cfg.title_fuzzy)
        if not (same_title or fuzzy_title):
            continue
        reasons = [f"same company '{new.company_key}'", "same title" if same_title else "similar title", f"location {loc}"]
        if sim is not None:
            reasons.append(f"description similarity {sim:.2f}")
        need = cfg.merge_similarity if loc == "match" else cfg.merge_similarity_unknown_location
        if same_title and sim is not None and sim >= need:
            consider(DedupDecision("merge", cid, round(0.5 + 0.5 * sim, 3), "cross_platform", reasons))
        elif sim is not None and sim >= cfg.review_similarity or (sim is None and same_title and loc == "match"):
            conf = round(0.4 + 0.5 * sim, 3) if sim is not None else 0.55
            consider(DedupDecision("flag", cid, conf, "possible", reasons + ["uncertain - needs review, not merged"]))
    return best
