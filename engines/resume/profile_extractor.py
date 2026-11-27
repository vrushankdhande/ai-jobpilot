"""Rule-based (Layer 1) extraction of a structured candidate profile.

Everything here is deterministic. Only information explicitly present in the
resume text is extracted; anything uncertain becomes a ``review_flag`` and
anything absent stays ``None`` / empty and is listed in ``missing_fields``.
"""
import re
from datetime import date

from ai.schemas.candidate_profile import (
    CandidateProfile,
    Certification,
    ContactInformation,
    Education,
    ProfessionalSummary,
    Project,
    Provenance,
    ReviewFlag,
    TechnicalSkills,
    WorkExperience,
)
from engines.resume.skills_taxonomy import (
    LOOKUP,
    SPOKEN_LABEL_RE,
    is_spoken_language,
    category_for_label,
    find_skills_in_text,
    normalize_skill,
)

# --------------------------------------------------------------------------
# Section detection
# --------------------------------------------------------------------------
BULLET_RE = re.compile(r"^\s*(?:[•\-\*–▪●◦■➢➤○]|\d{1,2}[.)])\s+")

SECTION_SYNONYMS: dict[str, list[str]] = {
    "summary": [
        "professional summary", "summary", "profile summary", "profile", "about me", "about",
        "career summary", "executive summary", "personal profile", "summary of qualifications",
    ],
    "objective": ["career objective", "objective", "professional objective"],
    "skills": [
        "technical skills", "skills", "core competencies", "technical expertise", "key skills",
        "skills and tools", "technical proficiencies", "skill set", "technical skill set",
        "areas of expertise", "skills summary", "programming skills", "technical skills and tools",
    ],
    "experience": [
        "work experience", "professional experience", "employment history", "experience",
        "work history", "career history", "internship experience", "relevant experience",
        "employment experience", "professional background", "relevant work experience", "professional work experience", "industry experience",
        "work experience and internships", "internships and work experience", "work and internship experience", "internships", "internship",
        "professional experience and internships", "experience and internships",
    ],
    "projects": [
        "projects", "key projects", "academic projects", "personal projects",
        "project experience", "notable projects", "selected projects", "self projects", "self project", "project", "personal project", "academic project",
        "major projects", "technical projects", "relevant projects", "side projects", "projects undertaken", "key project",
    ],
    "achievements": [
        "achievements", "accomplishments", "awards", "honors", "honours",
        "awards and achievements", "key achievements", "awards and honors",
        "competitions and seminars", "competitions", "seminars", "hackathons", "hackathons and competitions", "competitions and hackathons",
        "content and publications", "blogs and publications", "publications and talks", "talks and publications", "blogs", "content",
    ],
    "education": [
        "education", "academic background", "academic qualifications", "qualifications",
        "educational qualifications", "academics", "education and training", "academic details", "educational background", "education details",
    ],
    "certifications": [
        "certifications", "certificates", "certification", "courses", "licenses and certifications",
        "courses and certifications", "training", "licenses", "professional certifications",
        "training and certifications", "certifications and courses", "courses and training",
    ],
    "contact": ["contact information", "contact", "personal details", "personal information", "contact details"],
    "other": [
        "languages known", "language proficiency", "spoken languages", "human languages", "language skills", "interests", "hobbies", "references",
        "declaration", "hobbies and interests", "extracurricular activities", "publications",
        "volunteering", "volunteer experience",
    ],
}
_HEADINGS = {h: sec for sec, hs in SECTION_SYNONYMS.items() for h in hs}
_INLINE_OK = set(SECTION_SYNONYMS["skills"] + SECTION_SYNONYMS["summary"])


def _norm_heading(line: str) -> str:
    s = line.strip().rstrip(":").strip().replace("&", "and")
    s = re.sub(r"[^A-Za-z ]", " ", s)
    return re.sub(r"\s+", " ", s).strip().lower()


_PAGE_FOOTER_RE = re.compile(r"^[A-Za-z][A-Za-z.'\- ]{2,40}\s*[•·|]\s*(?:page\s*)?\d{1,2}(?:\s*(?:/|of)\s*\d{1,2})?$", re.IGNORECASE)


def split_sections(text: str) -> dict[str, list[str]]:
    """Split text into {section: [lines]}. Preface lines go under ``header``."""
    sections: dict[str, list[str]] = {"header": []}
    current = "header"
    for raw in text.split("\n"):
        line = raw.strip()
        if line and _PAGE_FOOTER_RE.match(line) and not BULLET_RE.match(line):
            continue                                              # "NAME • 2": a running page header/footer, not resume content
        if not line:
            sections.setdefault(current, []).append("")
            continue
        if len(line) <= 60 and not BULLET_RE.match(line):
            key = _norm_heading(line)
            if key in _HEADINGS:
                current = _HEADINGS[key]
                sections.setdefault(current, [])
                continue
        m = re.match(r"^([A-Za-z &/]{3,40}):\s*(.+)$", line)
        if m and _norm_heading(m.group(1)) in _INLINE_OK and not BULLET_RE.match(line):
            current = _HEADINGS[_norm_heading(m.group(1))]
            sections.setdefault(current, []).append(m.group(2))
            continue
        sections.setdefault(current, []).append(line)
    return sections


def merge_wrapped(lines: list[str]) -> list[str]:
    """Re-join lines that were wrapped mid-sentence (lowercase continuation)."""
    out: list[str] = []
    for ln in lines:
        s = ln.strip()
        if not s:
            out.append("")
        elif out and out[-1] and s[0].islower() and not BULLET_RE.match(s):
            out[-1] += " " + s
        else:
            out.append(s)
    return out


def strip_bullet(line: str) -> str:
    return BULLET_RE.sub("", line, count=1).strip()


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_DATE_TOKEN = (
    r"(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?,?\s+\d{4}"
    r"|\d{1,2}/\d{4}|\d{4}-\d{2}|\d{4})"
)
_PRESENT = r"(?:Present|Current|Currently|Till\s+date|To\s+date|Now|Ongoing|Today)"
DATE_RANGE_RE = re.compile(
    rf"(?<![A-Za-z])(?P<s>{_DATE_TOKEN})\s*(?:-|–|—|to|until)\s*(?P<e>{_DATE_TOKEN}|{_PRESENT})(?![\w])",
    re.IGNORECASE,
)
_PRESENT_RE = re.compile(rf"^{_PRESENT}$", re.IGNORECASE)


def parse_date(token: str) -> str | None:
    """Normalise a date token to ``YYYY-MM`` or ``YYYY`` (None if unparseable)."""
    t = token.strip().rstrip(".,")
    m = re.match(r"^([A-Za-z]+)\.?,?\s+(\d{4})$", t)
    if m:
        mon = _MONTHS.get(m.group(1)[:3].lower())
        return f"{m.group(2)}-{mon:02d}" if mon else None
    m = re.match(r"^(\d{1,2})/(\d{4})$", t)
    if m and 1 <= int(m.group(1)) <= 12:
        return f"{m.group(2)}-{int(m.group(1)):02d}"
    m = re.match(r"^(\d{4})-(\d{2})$", t)
    if m and 1 <= int(m.group(2)) <= 12:
        return t
    if re.match(r"^\d{4}$", t):
        return t
    return None


def _month_index(d: str, end: bool) -> int:
    y = int(d[:4])
    m = int(d[5:7]) if len(d) > 4 else (12 if end else 1)
    return y * 12 + m


def employment_intervals(jobs: list[WorkExperience], today: date | None = None):
    """[(start_month, end_month, job_index)] for jobs that have a start date."""
    today = today or date.today()
    out = []
    for i, job in enumerate(jobs):
        if not job.start_date:
            continue
        s = _month_index(job.start_date, end=False)
        if job.is_current or not job.end_date:
            e = today.year * 12 + today.month
        else:
            e = _month_index(job.end_date, end=True)
        out.append((s, e, i))
    return out


def calculate_total_experience(jobs: list[WorkExperience], today: date | None = None) -> float | None:
    """Union of employment months / 12. None unless every job has a start date."""
    if not jobs or any(not j.start_date for j in jobs):
        return None
    spans = sorted((s, e) for s, e, _ in employment_intervals(jobs, today))
    total, cur_s, cur_e = 0, None, None
    for s, e in spans:
        if e < s:
            return None
        if cur_e is None or s > cur_e + 1:
            if cur_e is not None:
                total += cur_e - cur_s + 1
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    total += cur_e - cur_s + 1
    return round(total / 12, 1)


def find_overlaps(jobs: list[WorkExperience], today: date | None = None) -> list[tuple[int, int]]:
    iv = employment_intervals(jobs, today)
    pairs = []
    for a in range(len(iv)):
        for b in range(a + 1, len(iv)):
            overlap = min(iv[a][1], iv[b][1]) - max(iv[a][0], iv[b][0]) + 1
            if overlap > 1:
                pairs.append((iv[a][2], iv[b][2]))
    return pairs


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
_TITLE_WORDS = (
    r"engineer|developer|scientist|analyst|manager|lead|architect|consultant|intern|associate|"
    r"director|specialist|researcher|administrator|designer|officer|head|programmer|trainee|"
    r"executive|president|founder|professor|assistant|technician|coordinator|strategist"
)
TITLE_RE = re.compile(rf"\b({_TITLE_WORDS})\b", re.IGNORECASE)
EMP_TYPE_RE = re.compile(
    r"\(?\b(full[- ]time|part[- ]time|contractor|contract|freelance|internship|permanent|temporary)\b\)?",
    re.IGNORECASE,
)
URL_RE = re.compile(r"(?:https?://)?(?:www\.)?[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}(?:/[^\s|,;]*)?")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<![\w.])(\+?\d[\d\s().\-]{8,17}\d)(?![\w])")
LOCATION_RE = re.compile(
    r"^[A-Z][A-Za-z.\- ]{1,30},\s*[A-Z][A-Za-z.\- ]{1,30}(?:,\s*[A-Z][A-Za-z.\- ]{1,30})?$"
)
_HAS_METRIC = re.compile(r"\d+(?:\.\d+)?\s*(?:%|x\b|percent|k\b|ms\b)|[$₹€£]\s?\d|\b\d[\d,]{3,}\b", re.IGNORECASE)
_HAS_AWARD = re.compile(
    r"\b(won|awarded|award|ranked|first place|second place|winner|recognized|recognised|promoted)\b",
    re.IGNORECASE,
)
_TECH_LABEL = re.compile(
    r"^(?:Technologies|Technology|Tech Stack|Tools|Stack|Environment)(?:\s+Used)?\s*:\s*(.+)$", re.IGNORECASE
)
_ROLE_LABEL = re.compile(r"^Role\s*:\s*(.+)$", re.IGNORECASE)


def is_achievement(text: str) -> bool:
    return bool(_HAS_METRIC.search(text) or _HAS_AWARD.search(text))


def _snippet(text: str, n: int = 200) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text[: n - 1] + "…" if len(text) > n else text


def _split_items(text: str) -> list[str]:
    parts = re.split(r"\s*[,;|•·]\s*|\s+and\s+", text)
    return [p.strip(" .") for p in parts if p.strip(" .")]


def _tech_from_text(text: str) -> list[str]:
    return [written for _, _, written in find_skills_in_text(text)]


def _dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for i in items:
        k = i.lower()
        if k not in seen:
            seen.add(k)
            out.append(i)
    return out


# --------------------------------------------------------------------------
# Contact
# --------------------------------------------------------------------------
def _norm_url(u: str) -> str:
    u = u.strip()
    while u.endswith(")") and u.count(")") > u.count("("):   # "Portfolio (https://x.io/)": the bracket belongs to the text, not the address
        u = u[:-1]
    u = u.rstrip("/.,;")
    return u if u.lower().startswith("http") else "https://" + u


def extract_contact(header_lines: list[str], full_text: str) -> tuple[ContactInformation, dict[str, Provenance]]:
    block_lines = [l for l in header_lines if l.strip()][:15] or [l for l in full_text.split("\n") if l.strip()][:15]
    block = "\n".join(block_lines)
    prov: dict[str, Provenance] = {}
    data: dict[str, str | None] = dict.fromkeys(
        ["full_name", "email", "phone", "location", "linkedin_url", "github_url", "portfolio_url"]
    )

    def put(field: str, value: str, snippet: str):
        data[field] = value
        prov[f"contact.{field}"] = Provenance(source_section="header", source_text=_snippet(snippet))

    em = EMAIL_RE.search(block) or EMAIL_RE.search(full_text)
    if em:
        put("email", em.group(0), em.group(0))

    no_email = EMAIL_RE.sub(" ", block)
    for m in PHONE_RE.finditer(no_email):
        digits = re.sub(r"\D", "", m.group(1))
        if 10 <= len(digits) <= 13 and not re.search(r"(19|20)\d{2}\s*[-–]\s*(19|20)\d{2}", m.group(1)):
            put("phone", m.group(1).strip(), m.group(0))
            break

    li = re.search(r"(?:https?://)?(?:www\.)?linkedin\.com/in/[\w\-%]+/?", block, re.IGNORECASE)
    if li:
        put("linkedin_url", _norm_url(li.group(0)), li.group(0))
    gh = re.search(r"(?:https?://)?(?:www\.)?github\.com/[\w\-]+", block, re.IGNORECASE)
    if gh:
        put("github_url", _norm_url(gh.group(0)), gh.group(0))
    for m in URL_RE.finditer(no_email):
        u = m.group(0)
        low = u.lower()
        if "linkedin.com" in low or "github.com" in low or "@" in u:
            continue
        if re.search(r"\.(?:com|dev|io|me|net|org|in|co|ai|xyz|app|tech|site|page)(?:/|$)", low):
            put("portfolio_url", _norm_url(u), u)
            break

    # name / location from "pieces" of the header lines
    first_nonempty = True
    for line in block_lines:
        pieces = [p.strip() for p in re.split(r"\s*[|•·]\s*", line) if p.strip()]
        for piece in pieces:
            lab = re.match(r"^(name|full name)\s*[:\-]\s*(.+)$", piece, re.IGNORECASE)
            if lab and not data["full_name"]:
                put("full_name", lab.group(2).strip(), line)
                continue
            loc = re.match(r"^(?:location|address|based in)\s*[:\-]\s*(.+)$", piece, re.IGNORECASE)
            if loc and not data["location"]:
                put("location", loc.group(1).strip(), line)
                continue
            if EMAIL_RE.search(piece) or URL_RE.fullmatch(piece) or PHONE_RE.search(piece):
                continue
            if not data["location"] and LOCATION_RE.match(piece) and not TITLE_RE.search(piece):
                put("location", piece, line)
                continue
            words = piece.split()
            if (
                first_nonempty
                and not data["full_name"]
                and 2 <= len(words) <= 4
                and all(re.match(r"^[A-Za-z][A-Za-z.'\-]*$", w) for w in words)
                and not TITLE_RE.search(piece)
            ):
                put("full_name", piece, line)
        first_nonempty = False
    return ContactInformation(**data), prov


# --------------------------------------------------------------------------
# Skills
# --------------------------------------------------------------------------
def extract_skills(
    skill_lines: list[str], full_text: str
) -> tuple[TechnicalSkills, list[ReviewFlag], dict[str, Provenance]]:
    cats = {k: [] for k in TechnicalSkills.model_fields}
    flags: list[ReviewFlag] = []
    prov: dict[str, Provenance] = {}
    seen: set[str] = set()

    def add(cat: str, text: str):
        key = normalize_skill(text)
        if not key or key in seen:
            return
        seen.add(key)
        cats[cat].append(text)

    if any(l.strip() for l in skill_lines):
        label_cat: str | None = None
        for raw in skill_lines:
            line = strip_bullet(raw)
            if not line:
                continue
            label, rest = None, line
            m = re.match(r"^([^:]{2,40}):\s*(.+)$", line)
            if m:
                label, rest = m.group(1), m.group(2)
                if SPOKEN_LABEL_RE.search(label):
                    label_cat = None
                    continue                                      # "Spoken Languages: English, German": not technical skills
                label_cat = category_for_label(label)
            elif "," not in line and len(line) <= 40 and category_for_label(line) and normalize_skill(line) not in LOOKUP:
                label_cat = category_for_label(line)  # sub-heading line
                continue
            for item in re.split(r"\s*[,;|•·]\s*", rest):
                item = item.strip(" .")
                item = re.sub(r"^and\s+", "", item, flags=re.IGNORECASE)
                if not item or len(item) > 40 or len(item.split()) > 5:
                    continue
                if is_spoken_language(item):
                    continue
                key = normalize_skill(item)
                if key not in LOOKUP and "/" in item:
                    sub = [s.strip() for s in item.split("/")]
                    if all(normalize_skill(s) in LOOKUP for s in sub):
                        for s in sub:
                            add(LOOKUP[normalize_skill(s)][0], s)
                        continue
                cat = LOOKUP[key][0] if key in LOOKUP else (label_cat or "other_skills")
                add(cat, item)
        prov["technical_skills"] = Provenance(
            source_section="skills", source_text=_snippet(" ".join(skill_lines)), method="rule_based"
        )
    else:
        for cat, _canon, written in find_skills_in_text(full_text):
            add(cat, written)
        if seen:
            flags.append(ReviewFlag(
                field="technical_skills",
                reason="No skills section found; skills were matched from explicit mentions elsewhere in the resume.",
            ))
            prov["technical_skills"] = Provenance(
                source_section="document", source_text=None, method="rule_based", kind="inferred"
            )
    return TechnicalSkills(**cats), flags, prov


# --------------------------------------------------------------------------
# Work experience
# --------------------------------------------------------------------------
def _parse_job_header(parts: list[str]) -> dict:
    text = " | ".join(parts)
    emp = EMP_TYPE_RE.search(text)
    employment_type = emp.group(1).strip().title() if emp else None
    if emp:
        text = EMP_TYPE_RE.sub(" ", text)
    segments = [s.strip(" ,|-–—") for s in re.split(r"\s*[|•·]\s*|\s+[–—-]\s+|\s+at\s+|\s+@\s+", text) if s.strip(" ,|-–—")]
    if len(segments) == 1 and "," in segments[0] and not LOCATION_RE.match(segments[0]):
        segments = [s.strip() for s in segments[0].split(",") if s.strip()]
    title = company = location = None
    for seg in segments:
        if title is None and TITLE_RE.search(seg):
            title = seg
        elif location is None and LOCATION_RE.match(seg) and not TITLE_RE.search(seg):
            location = seg
        elif company is None:
            company = seg
    return {"company_name": company, "job_title": title, "location": location, "employment_type": employment_type}


def extract_experience(lines: list[str]) -> tuple[list[WorkExperience], list[ReviewFlag], dict[str, Provenance]]:
    lines = merge_wrapped(lines)
    date_idx = [
        i for i, l in enumerate(lines)
        if l and not BULLET_RE.match(l) and len(l) <= 120 and DATE_RANGE_RE.search(l)
    ]
    entries, flags, prov = [], [], {}
    starts: list[int] = []
    pres: list[list[str]] = []
    prev = -1
    for i in date_idx:
        pre, j = [], i - 1
        while j > prev and len(pre) < 2 and lines[j] and not BULLET_RE.match(lines[j]) \
                and len(lines[j]) <= 90 and not lines[j].endswith((".", ";")):
            pre.insert(0, lines[j])
            j -= 1
        starts.append(j + 1)
        pres.append(pre)
        prev = i

    for n, i in enumerate(date_idx):
        end_body = starts[n + 1] if n + 1 < len(date_idx) else len(lines)
        m = DATE_RANGE_RE.search(lines[i])
        remainder = (lines[i][: m.start()] + " " + lines[i][m.end():]).strip(" |,-–—()•·")
        header_parts = pres[n] + ([remainder] if remainder else [])
        hdr = _parse_job_header(header_parts)

        start = parse_date(m.group("s"))
        end_tok = m.group("e")
        is_current = bool(_PRESENT_RE.match(end_tok.strip()))
        end = None if is_current else parse_date(end_tok)

        body = list(lines[i + 1:end_body])
        if hdr["job_title"] is None and hdr["company_name"]:
            # Layout "COMPANY | City  Mon YYYY - Mon YYYY" with the job title on the NEXT line. Only used when no title was found in the header,
            # and only for a short plain line (not a bullet, a sentence, a date or a "Technologies:" label) - never guessed from anything else.
            for k, nxt in enumerate(body):
                if not nxt:
                    continue
                cand = nxt.strip()
                if (not BULLET_RE.match(cand) and len(cand) <= 70 and not cand.endswith((".", ";", ":")) and len(cand.split()) <= 7
                        and not DATE_RANGE_RE.search(cand) and not _TECH_LABEL.match(cand) and not re.search(r"\d", cand)):
                    hdr["job_title"] = cand
                    body.pop(k)
                break                                                    # only the first non-blank line after the dates can be the title
        desc_lines, resp, ach, techs = [], [], [], []
        for raw in body:
            if not raw:
                continue
            tm = _TECH_LABEL.match(strip_bullet(raw))
            if tm:
                techs += _split_items(tm.group(1))
            elif BULLET_RE.match(raw):
                (ach if is_achievement(strip_bullet(raw)) else resp).append(strip_bullet(raw))
            else:
                desc_lines.append(raw)
        body_text = " ".join(lines[i + 1:end_body])
        techs = _dedupe(techs + _tech_from_text(body_text))

        job = WorkExperience(
            company_name=hdr["company_name"], job_title=hdr["job_title"],
            employment_type=hdr["employment_type"], location=hdr["location"],
            start_date=start, end_date=end, is_current=is_current,
            description=" ".join(desc_lines) or None,
            responsibilities=resp, technologies_used=techs, achievements=ach,
        )
        idx = len(entries)
        entries.append(job)
        prov[f"work_experience.{idx}"] = Provenance(
            source_section="experience", source_text=_snippet(" | ".join(header_parts + [lines[i]]))
        )
        label = f"work_experience[{idx}]"
        if not job.company_name:
            flags.append(ReviewFlag(field=f"{label}.company_name", reason="Company name could not be identified."))
        if not job.job_title:
            flags.append(ReviewFlag(field=f"{label}.job_title", reason="Job title could not be identified."))
        if not start or (not is_current and not end):
            flags.append(ReviewFlag(field=f"{label}.dates", reason="Start or end date could not be parsed."))
        elif len(start) == 4 or (end and len(end) == 4):
            flags.append(ReviewFlag(field=f"{label}.dates", reason="Only the year is given; month is unknown."))
    return entries, flags, prov


# --------------------------------------------------------------------------
# Projects
# --------------------------------------------------------------------------
def extract_projects(lines: list[str]) -> tuple[list[Project], dict[str, Provenance]]:
    lines = merge_wrapped(lines)
    projects: list[dict] = []
    cur: dict | None = None
    prev_kind = "blank"  # blank | bullet | title | other
    first = next((l for l in lines if l), "")
    if BULLET_RE.match(first):
        # Layout where EVERY project is one bullet: "• Name (tag) - description" (continuation lines belong to the previous project). The name is the text
        # before the first dash separator, verbatim; nothing is invented. Used only when the section starts with a bullet (the other layout has a title line first).
        for raw in lines:
            if not raw:
                continue
            if BULLET_RE.match(raw):
                text = strip_bullet(raw)
                m = re.match(r"^(?P<h>.{2,100}?)\s+[–—-]\s+(?P<d>.+)$", text)
                cur = {"title_line": (m.group("h") if m else text[:100]).strip(), "desc": [m.group("d").strip()] if m else ([] if len(text) <= 100 else [text]), "bullets": [], "techs": [], "role": None}
                projects.append(cur)
            elif cur is not None:
                cur["desc"].append(raw.strip())
        lines = []                                                     # handled; skip the heading-based pass below
    for raw in lines:
        if not raw:
            prev_kind = "blank"
            continue
        is_bullet = bool(BULLET_RE.match(raw))
        text = strip_bullet(raw)
        tm, rm = _TECH_LABEL.match(text), _ROLE_LABEL.match(text)
        if is_bullet:
            if cur is not None:
                cur["bullets"].append(text)
            prev_kind = "bullet"
        elif tm and cur is not None:
            cur["techs"] += _split_items(tm.group(1))
            prev_kind = "other"
        elif rm and cur is not None:
            cur["role"] = rm.group(1).strip()
            prev_kind = "other"
        elif cur is None or prev_kind in ("blank", "bullet"):
            if len(text) <= 100 and not text.endswith("."):
                cur = {"title_line": text, "desc": [], "bullets": [], "techs": [], "role": None}
                projects.append(cur)
                prev_kind = "title"
            elif cur is not None:
                cur["desc"].append(text)
                prev_kind = "other"
        else:
            cur["desc"].append(text)
            prev_kind = "other"

    out: list[Project] = []
    prov: dict[str, Provenance] = {}
    for p in projects:
        title_line = p["title_line"]
        url = None
        um = re.search(r"https?://\S+", title_line + " " + " ".join(p["desc"] + p["bullets"]))
        if um:
            url = um.group(0).rstrip(".,;)")
            title_line = title_line.replace(um.group(0), "").strip(" |-–—()")
        parts = [s.strip() for s in re.split(r"\s+[|–—-]\s+", title_line) if s.strip()]
        name = parts[0] if parts else None
        desc = " ".join(([" – ".join(parts[1:])] if len(parts) > 1 else []) + p["desc"]) or None
        resp = [b for b in p["bullets"] if not is_achievement(b)]
        ach = [b for b in p["bullets"] if is_achievement(b)]
        techs = _dedupe(p["techs"] + (_tech_from_text(" ".join(p["bullets"] + p["desc"])) if not p["techs"] else []))
        if not name:
            continue
        idx = len(out)
        out.append(Project(project_name=name, description=desc, technologies=techs, role=p["role"],
                           responsibilities=resp, achievements=ach, project_url=url))
        prov[f"projects.{idx}"] = Provenance(source_section="projects", source_text=_snippet(p["title_line"]))
    return out, prov


# --------------------------------------------------------------------------
# Education
# --------------------------------------------------------------------------
_DEGREE = (
    r"(?<![A-Za-z])(?:B\.?\s?Tech|B\.?\s?E\.?|B\.?\s?Sc\.?|BCA|B\.?\s?Com\.?|B\.A\.?|BS|B\.S\.?|"
    r"M\.?\s?Tech|M\.?\s?E\.?|M\.?\s?Sc\.?|MCA|M\.?\s?Com\.?|M\.A\.?|MS|M\.S\.?|MBA|Ph\.?D|"
    r"Bachelor(?:'s)?(?:\s+of\s+[A-Za-z]+(?:\s+(?!in\b)[A-Za-z]+)?)?|"
    r"Master(?:'s)?(?:\s+of\s+[A-Za-z]+(?:\s+(?!in\b)[A-Za-z]+)?)?|"
    r"Diploma|Associate [Dd]egree|Higher Secondary|Secondary School|HSC|SSC|12th|10th|Intermediate)(?![A-Za-z])"
)
DEGREE_RE = re.compile(_DEGREE)  # case sensitive on purpose (avoids "be", "ms")
INSTITUTION_RE = re.compile(r"(University|Institute|College|School|Academy|Polytechnic|\bIIT\b|\bNIT\b|\bIIIT\b|\bBITS\b)")
_GRADE_RE = re.compile(
    r"(?:CGPA|GPA|Percentage|Percent|Grade|Score)\s*[:\-]?\s*[\d.]+(?:\s*/\s*\d+(?:\.\d+)?|\s*%)?|\b\d{2,3}(?:\.\d+)?\s*%",
    re.IGNORECASE,
)


def extract_education(lines: list[str]) -> tuple[list[Education], dict[str, Provenance]]:
    groups: list[list[str]] = [[]]
    for raw in lines:
        line = strip_bullet(raw) if raw else ""
        if not line:
            if groups[-1]:
                groups.append([])
            continue
        has_deg = bool(DEGREE_RE.search(line))
        if has_deg and any(DEGREE_RE.search(g) for g in groups[-1]):
            groups.append([])
        groups[-1].append(line)

    out: list[Education] = []
    prov: dict[str, Provenance] = {}
    for g in filter(None, groups):
        text = " ".join(g)
        deg_m = DEGREE_RE.search(text)
        inst = None
        for line in g:
            for seg in re.split(r"\s*[,|–—]\s*|\s+-\s+", line):
                if INSTITUTION_RE.search(seg):
                    inst = re.sub(r"\b(?:19|20)\d{2}\b.*$", "", seg).strip(" ,-–—()")
                    break
            if inst:
                break
        if not deg_m and not inst:
            continue
        degree = spec = None
        if deg_m:
            degree = deg_m.group(0).strip()
            deg_line = next(l for l in g if DEGREE_RE.search(l))
            tail = deg_line[DEGREE_RE.search(deg_line).end():]
            sm = re.match(r"^\s*(?:in|\(|-|–|—|,)\s*([A-Za-z&][A-Za-z&\s]{2,50}?)\s*(?:\)|,|\||–|—|\bfrom\b|\d|$)", tail)
            if sm and not INSTITUTION_RE.search(sm.group(1)):
                spec = sm.group(1).strip()
        years = [int(y) for y in re.findall(r"\b((?:19|20)\d{2})\b", text)]
        start = grad = None
        if len(years) >= 2:
            start, grad = min(years[:2]), max(years[:2])
        elif len(years) == 1:
            grad = years[0]
        gm = _GRADE_RE.search(text)
        idx = len(out)
        out.append(Education(degree=degree, specialization=spec, institution=inst,
                             start_year=start, graduation_year=grad, grade=gm.group(0).strip() if gm else None))
        prov[f"education.{idx}"] = Provenance(source_section="education", source_text=_snippet(text))
    return out, prov


# --------------------------------------------------------------------------
# Certifications
# --------------------------------------------------------------------------
def extract_certifications(lines: list[str]) -> tuple[list[Certification], dict[str, Provenance]]:
    out: list[Certification] = []
    prov: dict[str, Provenance] = {}
    for raw in lines:
        line = strip_bullet(raw) if raw else ""
        if len(line) < 4:
            continue
        work = line
        cred_id = cred_url = issue = expiry = None
        um = re.search(r"https?://\S+", work)
        if um:
            cred_url = um.group(0).rstrip(".,;)")
            work = work.replace(um.group(0), " ")
        im = re.search(r"(?:Credential ID|Certificate ID|Credential No\.?|License No\.?|ID)\s*[:#]\s*([A-Za-z0-9\-_/]{3,})", work, re.IGNORECASE)
        if im:
            cred_id = im.group(1)
            work = work.replace(im.group(0), " ")
        xm = re.search(rf"(?:Expires?|Expiry|Valid (?:until|till))\s*[:\-]?\s*({_DATE_TOKEN})", work, re.IGNORECASE)
        if xm:
            expiry = parse_date(xm.group(1))
            work = work.replace(xm.group(0), " ")
        dm = re.search(_DATE_TOKEN, work)
        if dm:
            issue = parse_date(dm.group(0))
            work = work.replace(dm.group(0), " ")
        work = re.sub(r"\(\s*\)|\bIssued\b|\bin\b\s*$", " ", work, flags=re.IGNORECASE)
        work = re.sub(r"\s+", " ", work).strip(" ,;|-–—()")
        parts = [p.strip(" ,;") for p in re.split(r"\s+[–—-]\s+|\s+by\s+|\s*\|\s*", work) if p.strip(" ,;")]
        if len(parts) == 1 and parts[0].count(",") == 1:
            parts = [p.strip() for p in parts[0].split(",")]
        name = parts[0] if parts else None
        issuer = parts[1] if len(parts) > 1 else None
        if not name:
            continue
        idx = len(out)
        out.append(Certification(certification_name=name, issuing_organization=issuer, issue_date=issue,
                                 expiry_date=expiry, credential_id=cred_id, credential_url=cred_url))
        prov[f"certifications.{idx}"] = Provenance(source_section="certifications", source_text=_snippet(line))
    return out, prov


# --------------------------------------------------------------------------
# Whole-profile assembly
# --------------------------------------------------------------------------
def _bullets_to_list(lines: list[str]) -> list[str]:
    return [strip_bullet(l) for l in merge_wrapped(lines) if l and strip_bullet(l)]


def extract_profile(text: str, resume_id: str) -> CandidateProfile:
    sections = split_sections(text)
    flags: list[ReviewFlag] = []
    prov: dict[str, Provenance] = {}

    contact_lines = sections.get("header", []) + sections.get("contact", [])
    contact, p = extract_contact(contact_lines, text)
    prov.update(p)

    skills, f, p = extract_skills(sections.get("skills", []), text)
    flags += f
    prov.update(p)

    experience, f, p = extract_experience(sections.get("experience", []))
    flags += f
    prov.update(p)
    projects, p = extract_projects(sections.get("projects", []))
    prov.update(p)
    education, p = extract_education(sections.get("education", []))
    prov.update(p)
    certs, p = extract_certifications(sections.get("certifications", []))
    prov.update(p)
    achievements = _bullets_to_list(sections.get("achievements", []))

    summary_text = " ".join(l for l in merge_wrapped(sections.get("summary", [])) if l) or None
    objective = " ".join(l for l in merge_wrapped(sections.get("objective", [])) if l) or None
    if summary_text:
        prov["summary.professional_summary"] = Provenance(source_section="summary", source_text=_snippet(summary_text))
    if objective:
        prov["summary.career_objective"] = Provenance(source_section="objective", source_text=_snippet(objective))

    # current job title: explicit title line under the name, else most recent role (inferred)
    title = None
    header_nonempty = [l for l in sections.get("header", []) if l.strip()]
    for line in header_nonempty[1:3]:
        if TITLE_RE.search(line) and not EMAIL_RE.search(line) and not re.search(r"\d", line) and len(line) <= 60:
            title = line.strip()
            prov["summary.current_job_title"] = Provenance(source_section="header", source_text=_snippet(line))
            break
    if not title and experience:
        current = [j for j in experience if j.is_current and j.job_title]
        latest = current[0] if current else next((j for j in experience if j.job_title), None)
        if latest:
            title = latest.job_title
            prov["summary.current_job_title"] = Provenance(
                source_section="experience", source_text=_snippet(title), method="calculated", kind="inferred"
            )
            flags.append(ReviewFlag(field="summary.current_job_title",
                                    reason="Taken from the most recent work-experience entry, not stated explicitly."))

    total = calculate_total_experience(experience)
    if total is not None:
        prov["summary.total_experience_years"] = Provenance(
            source_section="experience", method="calculated", kind="inferred",
            source_text="Calculated from employment dates (overlaps counted once).",
        )
        flags.append(ReviewFlag(field="summary.total_experience_years",
                                reason="Calculated from employment dates; please confirm."))
    elif experience:
        flags.append(ReviewFlag(field="summary.total_experience_years",
                                reason="Not calculated: one or more roles have no parseable start date."))
    for a, b in find_overlaps(experience):
        flags.append(ReviewFlag(field="work_experience",
                                reason=f"Employment entries #{a + 1} and #{b + 1} overlap in time."))

    summary = ProfessionalSummary(
        professional_summary=summary_text, current_job_title=title,
        total_experience_years=total, career_objective=objective,
    )

    profile = CandidateProfile(
        resume_id=resume_id, contact=contact, summary=summary, technical_skills=skills,
        work_experience=experience, projects=projects, education=education,
        certifications=certs, achievements=achievements,
        review_flags=flags, provenance=prov,
    )
    profile.missing_fields = compute_missing_fields(profile)
    profile.parsing_status = "completed_with_warnings" if (flags or profile.missing_fields) else "completed"
    return profile


def compute_missing_fields(profile: CandidateProfile) -> list[str]:
    missing = [f"contact.{k}" for k, v in profile.contact.model_dump().items() if not v]
    if not profile.summary.professional_summary:
        missing.append("summary.professional_summary")
    if not profile.summary.current_job_title:
        missing.append("summary.current_job_title")
    if profile.summary.total_experience_years is None:
        missing.append("summary.total_experience_years")
    if not profile.technical_skills.all_skills():
        missing.append("technical_skills")
    for name in ("work_experience", "projects", "education", "certifications"):
        if not getattr(profile, name):
            missing.append(name)
    return missing
