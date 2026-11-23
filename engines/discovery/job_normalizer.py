"""RawJob (source-specific) -> JobRecord (one consistent format). Never invents values.

Skills, experience range, salary and work mode are extracted from the description with the Phase 3 JD parser, so a
discovered job and a pasted job are interpreted identically. Source values are preserved on ``SourceRef.original``.
"""
import html
import re
from datetime import datetime, timedelta, timezone

from ai.schemas.job_schema import JobInput
from database.models.job import JobRecord, SourceRef
from engines.discovery.deduplication import canonical_url, company_key, location_keys, title_key
from engines.matching.jd_parser import _clean_locations, _norm_emp, _work_mode, parse_jd, parse_salary
from engines.matching.skill_extractor import SkillCatalog
from integrations.base_adapter import RawJob

NORMALIZER_VERSION = "1.0"
MAX_DESCRIPTION = 60_000


class InvalidJob(Exception):
    """The record cannot be used (reason is safe to show)."""


# ---------------------------------------------------------------------------------- text fields
def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    v = re.sub(r"\s+", " ", html.unescape(str(value))).strip()
    return v or None


def clean_title(title: str | None) -> str | None:
    t = clean_text(title)
    if not t:
        return None
    t = re.sub(r"\s*[\(\[]\s*(?:m\s*/\s*f\s*/\s*[dx]|f\s*/\s*m\s*/\s*[dx]|all genders?|w\s*/\s*m\s*/\s*d)\s*[\)\]]", "", t, flags=re.I)
    t = re.sub(r"\s*[-–—|]\s*(?:req(?:uisition)?|r|jr)?[-_ #]*\d{4,}\s*$", "", t, flags=re.I)  # trailing requisition id
    return t.strip(" -–—|") or None


def clean_company(name: str | None) -> str | None:
    c = clean_text(name)
    if not c:
        return None
    return re.sub(r"^careers? at\s+", "", c, flags=re.I) or None


# ------------------------------------------------------------------------------------------- dates
_REL = re.compile(r"(\d+)\s*\+?\s*(minute|hour|day|week|month)s?\s+ago", re.I)


def parse_date_any(value, now: datetime | None = None) -> datetime | None:
    """ISO strings, epoch seconds/milliseconds, 'Nov 3, 2026', '3 days ago', 'today', 'yesterday'. None if unknown."""
    now = now or datetime.now(timezone.utc)
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        v = float(value)
        if v > 1e11:
            v /= 1000.0
        try:
            return datetime.fromtimestamp(v, tz=timezone.utc) if v > 0 else None
        except (OverflowError, OSError, ValueError):
            return None
    s = str(value).strip()
    if not s:
        return None
    low = s.lower()
    if low in ("today", "just now", "just posted", "posted today"):
        return now
    if low in ("yesterday", "posted yesterday"):
        return now - timedelta(days=1)
    m = _REL.search(low)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        return now - timedelta(**{unit + "s": n if unit != "month" else n * 30})
    if re.fullmatch(r"\d{10,13}", s):
        return parse_date_any(int(s), now)
    iso = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


# -------------------------------------------------------------------------------------- main entry
def normalize(raw: RawJob, catalog: SkillCatalog, bands: dict[str, float] | None = None, now: datetime | None = None) -> JobRecord:
    now = now or datetime.now(timezone.utc)
    title = clean_title(raw.title)
    company = clean_company(raw.company)
    description = (raw.description or "").strip() or None
    if description and len(description) > MAX_DESCRIPTION:
        description = description[:MAX_DESCRIPTION]
    if not title and not description and not raw.url:
        raise InvalidJob("record has no title, description or URL")

    warnings: list[str] = []
    if not title:
        warnings.append("title unknown")
    if not company:
        warnings.append("company unknown")
    if not description:
        warnings.append("description unknown")

    locations: list[str] = []
    for text in raw.location_text:
        for loc in _clean_locations(clean_text(text) or ""):
            if loc not in locations:
                locations.append(loc)

    analysis = None
    if description and len(description) >= 30 and re.search(r"[A-Za-z]{3,}.*[A-Za-z]{3,}", description):
        try:
            analysis = parse_jd(JobInput(job_description=description, job_title=title, company_name=company,
                                         location=", ".join(locations) or None, employment_type=raw.employment_text,
                                         salary=raw.salary_text), catalog, bands=bands)
        except Exception:
            warnings.append("description could not be analysed")
    elif description:
        warnings.append("description too short to analyse")

    # work mode
    work_mode = raw.work_mode_text if raw.work_mode_text in ("remote", "hybrid", "onsite") else None
    if work_mode is None:
        work_mode, _ = _work_mode((description or "")[:4000], ", ".join(locations + raw.location_text))
    # employment type (source field first, then wording in the description)
    emp = _norm_emp((raw.employment_text or "").replace("_", "-")) or (analysis.employment_type if analysis else None)
    # salary: structured values first, then text
    smin, smax, cur = raw.salary_min, raw.salary_max, (raw.salary_currency or "").upper() or None
    if smin is None and smax is None:
        parsed = parse_salary(raw.salary_text) if raw.salary_text else (None, None, None, None)
        smin, smax, cur = parsed[0], parsed[1], parsed[2]
        if smin is None and analysis:
            smin, smax, cur = analysis.minimum_salary, analysis.maximum_salary, analysis.currency
    if smin is not None and smax is not None and smin > smax:
        smin = smax = cur = None
        warnings.append("salary range looked inverted; ignored")
    if cur and not re.fullmatch(r"[A-Z]{3}", cur):
        cur = None

    posted = parse_date_any(raw.posted_raw, now)
    if raw.posted_raw not in (None, "") and posted is None:
        warnings.append("posted date could not be parsed")
    if posted and posted > now + timedelta(days=1):
        warnings.append("posted date is in the future; ignored")
        posted = None

    canon = canonical_url(raw.url)
    if raw.url and not canon:
        warnings.append("job URL is not a valid public http(s) URL; ignored")
    emp_ok = emp if emp in ("full-time", "part-time", "contract", "internship", "freelance", "temporary", "permanent") else None

    ref = SourceRef(
        source=raw.source, provider=raw.provider, source_job_id=str(raw.source_job_id) if raw.source_job_id else None,
        url=raw.url if canon else None, canonical_url=canon, first_seen_at=now, last_seen_at=now,
        original={"job_title": raw.title, "company_name": raw.company, "location": raw.location_text,
                  "employment_type": raw.employment_text, "work_mode": raw.work_mode_text, "salary_text": raw.salary_text,
                  "posted": str(raw.posted_raw) if raw.posted_raw is not None else None, "department": raw.department,
                  **{k: v for k, v in (raw.original or {}).items() if v is not None}},
    )
    record = JobRecord(
        source=raw.source, source_job_id=ref.source_job_id, job_title=title, company_name=company, job_description=description,
        mandatory_skills=analysis.mandatory_skills if analysis else [], preferred_skills=analysis.preferred_skills if analysis else [],
        experience_min=analysis.minimum_experience_years if analysis else None,
        experience_max=analysis.maximum_experience_years if analysis else None,
        location=locations, work_mode=work_mode, employment_type=emp_ok, salary_min=smin, salary_max=smax, currency=cur,
        job_url=canon, posted_date=posted, discovered_at=now, last_updated_at=now,
        source_refs=[ref], title_key=title_key(title), company_key=company_key(company), location_keys=location_keys(locations),
        canonical_url=canon, warnings=warnings, normalizer_version=NORMALIZER_VERSION,
    )
    record.content_hash = record.compute_content_hash()
    ref.content_hash = record.content_hash
    return record
