"""Job-description parser: deterministic rules, optionally refined by Claude.

Key rule: a keyword in a JD is NOT automatically mandatory. Each skill is classified as
``mandatory`` / ``preferred`` / ``example`` / ``mentioned`` from the section it appears in and the
words around it ("required", "nice to have", "such as", ...). Anything Claude adds must be
found in the JD text, and Claude never changes numeric scores.
"""
import hashlib
import logging
import re

from ai.claude_client import AIResponseError, AIUnavailable, ClaudeClient, load_prompt
from ai.schemas.job_schema import (
    AIJobExtraction,
    CertificationRequirement,
    EducationRequirement,
    JobAnalysis,
    JobInput,
    SENIORITY_LEVELS,
    SkillExperience,
    SkillRequirement,
)
from engines.matching.skill_extractor import SkillCatalog, SkillMention, get_catalog, skill_key

logger = logging.getLogger(__name__)

DEFAULT_BANDS = {"entry": 0, "junior": 1, "mid": 3, "senior": 5, "lead": 8, "principal": 12}
_RANK = {"mandatory": 3, "preferred": 2, "mentioned": 1, "example": 0}

# --------------------------------------------------------------------------- sections
_HEADS = {
    "required": [
        "requirements", "required", "required skills", "required qualifications", "must have", "must haves",
        "qualifications", "what you need", "what you'll need", "what we are looking for", "what we're looking for",
        "basic qualifications", "minimum qualifications", "skills required", "skills and experience",
        "your profile", "who you are", "technical requirements", "key requirements", "experience required",
        "required experience", "mandatory skills", "skills", "technical skills", "key skills", "requirement",
        "what you bring", "you have", "you should have", "about you", "candidate profile", "eligibility",
        "what we look for", "what we look for in you", "what you'll bring", "your expertise", "who you might be", "who you may be",
        "required skills and experience", "the ideal candidate", "ideal candidate", "success in this role requires strength in four areas",
        "what you'll need to succeed", "skills and qualifications", "essential skills", "essential requirements",
    ],
    "preferred": [
        "preferred", "preferred qualifications", "preferred skills", "nice to have", "nice to haves", "good to have",
        "bonus", "bonus points", "desired", "desirable", "additional skills", "pluses", "preferred requirements",
        "preferred experience", "good to have skills", "nice-to-have", "extra credit", "a plus", "plus",
    ],
    "responsibilities": [
        "responsibilities", "key responsibilities", "what you'll do", "what you will do", "role and responsibilities",
        "roles and responsibilities", "duties", "the role", "your role", "day to day", "your impact",
        "job responsibilities", "what you'll be doing", "in this role you will", "key duties",
        "the impact you will have", "impact you will have", "the impact you'll have", "the difference you will make", "a typical day",
        "the opportunity", "role description", "what you will own", "what you'll own", "how you'll make an impact",
    ],
    "ignore": [
        "about us", "about the company", "about", "who we are", "benefits", "perks", "what we offer", "why join us",
        "company overview", "equal opportunity", "how to apply", "our culture", "compensation", "about the team",
        "our mission", "why us", "life at",
        "mission", "compliance", "relocation statement", "in office requirement statement", "the community you will join",
        "team description", "about the team", "please note",
    ],
}
_HEAD_LOOKUP = {h: sec for sec, hs in _HEADS.items() for h in hs}
# Company/legal/pay boilerplate headings ("About Databricks", "Pay Range Transparency", "Our Commitment to Diversity"): never requirements.
_IGNORE_HEAD_RX = re.compile(
    r"^(about\b|pay range|pay transparency|salary range|compensation|benefits|our commitment|commitment to|diversity|equal\b|eeo\b|privacy|"
    r"export|legal|disclaimer|how to apply|why\b|life at|our (?:culture|values|mission|story|team)|what we offer|perks|accessibility|"
    r"data protection|notice to)", re.I)


_TYPOGRAPHY = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', " ": " ", "​": "", "‑": "-"})


def _norm_heading(line: str) -> str:
    s = re.sub(r"^[\s#>*_\-•\d.)]+", "", line.strip())
    s = re.sub(r"[*_]+", "", s).rstrip(":").strip()
    s = re.sub(r"[^a-z' ]", " ", s.lower().replace("&", "and"))
    return re.sub(r"\s+", " ", s).strip()


_BULLET = re.compile(r"^\s*(?:[•\-\*–—▪●◦■➢➤○]|\d{1,2}[.)])\s+")


def _looks_like_heading(raw: str, plain: str) -> bool:
    """A short Title-Case line without bullet or sentence punctuation ("Pay Range Transparency", "Our Commitment to Diversity")."""
    if _BULLET.match(raw) or len(plain) > 60 or re.search(r"[.!?;,]$|\d|@|https?:", plain) or ":" in plain[:-1]:     # "Location: Pune" is data, not a heading
        return False
    words = plain.rstrip(":").split()
    if not 2 <= len(words) <= 8:
        return plain.endswith(":") and 1 <= len(words) <= 8 and plain[:1].isupper()
    small = {"and", "or", "of", "the", "to", "for", "in", "a", "an", "at", "on", "with", "our", "your", "&"}
    return all(w[:1].isupper() or w.lower() in small for w in words)


def tag_lines(text: str) -> list[tuple[str, str]]:
    """[(line, section)] where section in general/required/preferred/responsibilities/ignore."""
    out, section = [], "general"
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        plain = re.sub(r"[*_#]+", "", line).strip()
        key = _norm_heading(plain)
        if len(plain) <= 70 and key in _HEAD_LOOKUP and not re.search(r"[.!?]$", plain):
            section = _HEAD_LOOKUP[key]
            continue
        if _looks_like_heading(raw, plain):          # an unrecognised heading ends the previous section instead of leaking into the next one
            section = "ignore" if _IGNORE_HEAD_RX.match(key) else "general"
            continue
        m = re.match(r"^[\s#>*_\-•]*([A-Za-z' &/\-]{3,40}?)\s*:\s*(.+)$", plain)
        if m and _norm_heading(m.group(1)) in _HEAD_LOOKUP:
            section = _HEAD_LOOKUP[_norm_heading(m.group(1))]
            out.append((m.group(2).strip(), section))
            continue
        out.append((plain, section))
    return out


def split_sentences(line: str) -> list[str]:
    line = _BULLET.sub("", line).strip()
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9•\-])|(?<=;)\s+", line)
    return [p.strip() for p in parts if p.strip()]


# --------------------------------------------------------------------------- cues
_NEG = re.compile(r"\b(not required|isn'?t required|not necessary|no need|not needed|without)\b|\bno\b.{0,40}\b(required|needed|necessary)\b", re.I)
_BOILER = re.compile(
    r"\b(equal opportunity|equal employment|committed to (?:fair|diversity|equal|providing|building)|export controls?|pay range|"
    r"compensation practices|privacy (?:notice|policy)|reasonable accommodations?|background check|drug test|applicable law)\b", re.I)
# The company's own history ("Building on more than 30 years of investing experience, Point72 ...", "a 25-year track record") is not a
# requirement for the candidate - found on a real posting that was read as "30+ years required".
_ORG_YEARS = re.compile(
    r"\b(?:building on|backed by|with|for|over|after|celebrating|drawing on)\s+(?:more than\s+|over\s+|nearly\s+|almost\s+)?\d+\s*\+?\s*years?\s+of\s+"
    r"(?:investing|investment|industry|history|heritage|excellence|innovation|operations?|growth|service|serving|business|leadership|success|trust)\b|"
    r"\b\d+\s*-?\s*years?\s+(?:track record|history|heritage|legacy)\b|\bfounded (?:in|over)\b", re.I)
# What a role DOES is not what a candidate must already have: skills named in responsibilities only count as requirements with explicit wording.
_EXPLICIT = re.compile(r"\b(required|requires|must|mandatory|essential|should have|need to have|needs to have|minimum|\d+\s*\+?\s*(?:years?|yrs?))\b", re.I)
_POSTHIRE = re.compile(
    r"\bwithin\s+\d+\s*(?:days?|weeks?|months?)\b|path to completion|upon (?:hire|joining)|after (?:hire|joining|you join)|once (?:hired|you join)|"
    r"will (?:be required|need) to (?:obtain|complete|earn|get)", re.I)
_PREF = re.compile(
    r"\b(preferred|prefer|nice[- ]to[- ]have|good to have|a plus|is a plus|are a plus|pluses|(?:huge|big|strong|great|real) plus(?:es)?|bonus|desirable|desired|"
    r"would be (?:a |an )?(?:plus|great|nice|advantage|bonus)|advantage|beneficial|optional|ideally|not mandatory|"
    r"(?:strong |keen )?willing(?:ness)? to learn|ability to learn|eager to learn)\b", re.I)
_STRONG = re.compile(
    r"\b(required|must|mandatory|essential|should have|need to have|needs to have|minimum|proficien\w+|strong|solid|"
    r"expert\w*|hands[- ]on|experience (?:in|with|of|using|on)|knowledge of|working knowledge|skilled in|"
    r"expertise (?:in|with)|command (?:of|over)|ability to (?:code|program|work with)|\d+\s*\+?\s*(?:years?|yrs?))\b", re.I)
_WEAK = re.compile(r"\b(familiar\w*|exposure|awareness|acquainted|understanding of)\b", re.I)
_EXAMPLE = re.compile(r"\b(such as|e\.g\.?|eg\.|for example|for instance|like|including)\b|\(e\.g", re.I)
# "at least three of the following: A, B, C, D" - a choose-N list: no single item is individually required.
_CHOOSE = re.compile(
    r"\b(?:at least|any|one|two|three|several|some)\s+(?:\w+\s+)?of\s+(?:the\s+)?(?:following|these|below)\b|"
    r"\b(?:at least one|one or more|any one|one or two)\b", re.I)
# A sentence about a degree names a FIELD of study ("degree in Statistics"), not a skill.
_DEGREE_WORDS = re.compile(r"\b(degree|ph\.?d|doctorate|master'?s?|bachelor'?s?|b\.?tech|m\.?tech|b\.?sc|m\.?sc)\b", re.I)


def _importance(sentence: str, section: str) -> str | None:
    if _BOILER.search(sentence):
        return None
    if _NEG.search(sentence):
        return "mentioned"
    if _PREF.search(sentence) or section == "preferred":
        return "preferred"
    if section == "ignore":
        return None
    if _WEAK.search(sentence) and not _STRONG.search(sentence):
        return "preferred"
    if section == "required":
        return "mandatory"
    if section == "responsibilities":
        return "mandatory" if _EXPLICIT.search(sentence) else "mentioned"
    return "mandatory" if _STRONG.search(sentence) else "mentioned"


def _connector(between: str) -> str | None:
    b = between.strip().lower()
    if re.fullmatch(r"(,\s*)?(and/or|or)", b) or b == "/":
        return "or"
    if re.fullmatch(r"(,|&|,?\s*and|,\s*&)", b):
        return "and"
    return None


def _skill_mentions(sentence: str, catalog: SkillCatalog) -> list[SkillMention]:
    found = catalog.find_in_text(sentence)
    seen = {m.canonical for m in found}
    # ambiguous names (R, Go, C, ...) only count as whole list items
    pos = 0
    for item in re.split(r"([,;/|])|\s+and\s+|\s+or\s+", sentence):
        if item is None:
            continue
        start = sentence.find(item, pos)
        pos = max(pos, start + len(item)) if start >= 0 else pos
        canon = catalog.list_item_skill(item) if 0 < len(item.strip()) <= 12 else None
        if canon and canon not in seen and catalog.skills[canon].get("ambiguous"):
            seen.add(canon)
            found.append(SkillMention(canon, item.strip(), max(start, 0), max(start, 0) + len(item)))
    return sorted(found, key=lambda m: m.start)


# --------------------------------------------------------------------------- experience
_YR = r"(?:years?|yrs?)"
_NUM = r"(\d{1,2}(?:\.\d)?)"
_RANGE = re.compile(rf"{_NUM}\s*\+?\s*(?:-|–|—|to)\s*{_NUM}\s*\+?\s*{_YR}", re.I)
_MINP = re.compile(rf"(?:minimum(?: of)?|min\.?|at least|atleast|over|more than)\s*{_NUM}\s*\+?\s*{_YR}", re.I)
_PLUS = re.compile(rf"{_NUM}\s*\+\s*{_YR}", re.I)
_PLAIN = re.compile(
    rf"{_NUM}\s*{_YR}\s*(?:of\s+)?(?:(?:relevant|professional|hands-on|industry|work|working|practical|total|overall)\s+)*(?:experience|exp\b)",
    re.I)
_MAXP = re.compile(rf"(?:up to|no more than|at most|maximum(?: of)?|not exceeding|less than)\s*{_NUM}\s*{_YR}", re.I)
_FRESHER = re.compile(r"\b(fresher|freshers|entry[- ]level|no (?:prior |previous )?experience (?:required|needed|necessary)|recent graduate)\b", re.I)
_ALT = re.compile(r"\b(holders?|alternatively|or equivalent|or\s+\d+\s*\+?\s*(?:years?|yrs?))\b", re.I)
_SKILL_TAIL = re.compile(r"^\s*(?:of\s+)?(?:(?:relevant|professional|hands-on|industry|practical|work|working|total)\s+)*(?:experience|exp)?\s*(?:in|with|using|of|on)\s+(.{1,80})", re.I)


def _parse_experience(sentence: str, catalog: SkillCatalog):
    """-> list of (kind, min, max, restrictive, skill|None) found in one sentence."""
    out, used = [], []

    def tail_skill(end: int):
        tail = _SKILL_TAIL.match(sentence[end:end + 120])
        if not tail:
            return None
        found = catalog.find_in_text(re.split(r"[.;]", tail.group(1))[0])
        return found[0].canonical if found else None

    for m in _MAXP.finditer(sentence):
        out.append(("max", None, float(m.group(1)), True, None))
        used.append(m.span())
    spans = []
    for rx, kind in ((_RANGE, "range"), (_MINP, "min"), (_PLUS, "min"), (_PLAIN, "min")):
        for m in rx.finditer(sentence):
            if any(m.start() < e and m.end() > s for s, e in used + spans):
                continue
            spans.append(m.span())
            if kind == "range":
                lo, hi = float(m.group(1)), float(m.group(2))
                if lo <= hi:
                    out.append(("general", lo, hi, False, tail_skill(m.end())))
            else:
                out.append(("general", float(m.group(1)), None, False, tail_skill(m.end())))
    return out


def _level_from_years(years: float, bands: dict[str, float]) -> str:
    level = SENIORITY_LEVELS[0]
    for name in SENIORITY_LEVELS:
        if years >= bands.get(name, 0):
            level = name
    return level


_TITLE_LEVEL = [
    (re.compile(r"\b(principal|architect|distinguished|director|head of)\b", re.I), "principal"),
    (re.compile(r"\b(lead|staff)\b", re.I), "lead"),
    (re.compile(r"\b(senior|sr\.?)\b", re.I), "senior"),
    (re.compile(r"\b(junior|jr\.?|associate)\b", re.I), "junior"),
    (re.compile(r"\b(intern|internship|trainee|graduate|entry[- ]level|fresher|apprentice)\b", re.I), "entry"),
    (re.compile(r"\b(mid[- ]level|intermediate)\b|\bII\b", re.I), "mid"),
]
_TITLE_WORDS = re.compile(r"\b(engineer|developer|scientist|analyst|manager|architect|specialist|consultant|intern|lead|administrator|designer|researcher|programmer)\b", re.I)

# --------------------------------------------------------------------------- education
_EDU_PATTERNS = [
    ("phd", 4, re.compile(r"\b(ph\.?d|doctorate|doctoral)\b", re.I)),
    ("master", 3, re.compile(r"\b(master(?:'s|’s)|masters\b|master of|master degree|m\.?\s?tech|m\.?\s?sc|mca|mba|m\.e\b)", re.I)),
    ("master", 3, re.compile(r"\b(MS|ME)\b(?!\s*(?:office|excel|word))")),
    ("bachelor", 2, re.compile(r"\b(bachelor(?:'s|s)?|b\.?\s?tech|b\.?\s?sc|bca|b\.e\b|b\.?com|undergraduate)\b", re.I)),
    ("bachelor", 2, re.compile(r"\b(BS|BE|BA)\b")),
    ("diploma", 1, re.compile(r"\bdiploma\b", re.I)),
]
# A bare "degree" (no level named) counts as bachelor-level - used only when no specific level matched.
_EDU_GENERIC = ("bachelor", 2, re.compile(r"\b(?:university|college)\s+degree\b|\bdegree\s+in\b|\b(?:a |an )?degree\b", re.I))
_EQUIV = re.compile(r"\b(or equivalent(?: (?:practical |work |professional )?experience)?|equivalent (?:practical |work |professional )?experience)\b", re.I)
_FIELD = re.compile(r"\b(?:in|of)\s+((?:[A-Z][A-Za-z&]+|and|or|/)(?:[ ,]+(?:[A-Z][A-Za-z&]+|and|or|/)){0,5})")

# --------------------------------------------------------------------------- misc patterns
_CERT = re.compile(
    r"((?:[A-Z][\w+&\-]*\s+){0,3}Certified(?:\s+[A-Z][\w+&\-]*){1,5}|"
    r"[A-Z][\w+&\-]*(?:\s+[A-Z][\w+&\-]*){0,3}\s+Certification(?:\s+[A-Z][\w+&\-]*){0,3})")
_CERT_LABEL_WORDS = {"mandatory", "required", "preferred", "relevant", "valid", "any", "industry", "professional", "technical", "additional"}
_CERT_TOKENS = re.compile(r"\b(PMP|CISSP|CISA|CISM|CEH|CKA|CKAD|CCNA|CCNP|ITIL|Six Sigma|CFA)\b")
_AUTH = re.compile(
    r"(must|required to|need to|should)\b.{0,60}\b(authori[sz]ed|right to work|eligible to work|citizen|permanent resident)|"
    r"authori[sz]ed to work|work authori[sz]ation|right to work|"
    r"(no|not|unable|cannot|can't|won't)\b.{0,30}\bsponsor|security clearance|\bcitizen(?:ship)? (?:is )?required", re.I)
_OTHER_REQ = re.compile(
    r"\b(willing(?:ness)? to (?:relocate|travel)|(?:must|should) be (?:willing|able) to (?:relocate|travel|work)|"
    r"rotational shifts?|night shifts?|on[- ]call|notice period|immediate joiner|travel (?:up to|required))\b", re.I)
_VERBS = re.compile(
    r"^(?:you will |will )?(design|develop|build|implement|deploy|maintain|lead|collaborate|work|analy[sz]e|create|manage|"
    r"optimi[sz]e|monitor|research|integrate|conduct|own|drive|ensure|write|test|support|define|evaluate|improve|"
    r"architect|automate|train|fine-tune|partner|mentor|present|document|prepare|translate|perform|participate|"
    r"identify|deliver|review|contribute|set up|operate|scale|clean|explore|experiment)\b", re.I)
_EMP = re.compile(r"\b(full[- ]time|part[- ]time|contract(?:or)?|internship|freelance|temporary|permanent)\b", re.I)


def _norm_emp(raw: str | None) -> str | None:
    if not raw:
        return None
    m = _EMP.search(raw) or re.search(r"\b(intern)\b", raw, re.I)
    if not m:
        return None
    v = m.group(1).lower().replace(" ", "-")
    return {"contractor": "contract", "intern": "internship"}.get(v, v)


# salary
_SAL_LPA = re.compile(r"(?:₹|INR|Rs\.?)?\s*(\d+(?:\.\d+)?)\s*(?:-|–|—|to)\s*(\d+(?:\.\d+)?)\s*(?:LPA|lakhs?|lacs?)", re.I)
_SAL_MONEY = re.compile(
    r"(\$|USD|€|EUR|£|GBP|₹|INR)\s*(\d[\d,]*(?:\.\d+)?)\s*(k)?\s*(?:-|–|—|to)\s*(?:\$|USD|€|EUR|£|GBP|₹|INR)?\s*(\d[\d,]*(?:\.\d+)?)\s*(k)?", re.I)
_CUR = {"$": "USD", "usd": "USD", "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP", "₹": "INR", "inr": "INR"}


def parse_salary(text: str | None):
    """-> (min, max, currency, warning|None). Annual figures only."""
    if not text:
        return None, None, None, None
    if re.search(r"\b(per hour|/hr|hourly|per month|/month|monthly)\b", text, re.I):
        return None, None, None, "Salary is not annual (hourly/monthly); not parsed."
    m = _SAL_LPA.search(text)
    if m:
        return float(m.group(1)) * 100000, float(m.group(2)) * 100000, "INR", None
    m = _SAL_MONEY.search(text)
    if m:
        lo, hi = float(m.group(2).replace(",", "")), float(m.group(4).replace(",", ""))
        if (m.group(3) or m.group(5)) and lo < 1000 and hi < 1000:
            lo, hi = lo * 1000, hi * 1000
        elif m.group(3):
            lo *= 1000 if lo < 1000 else 1
        if lo > hi:
            return None, None, None, "Salary range looks inverted; not parsed."
        return lo, hi, _CUR[m.group(1).lower()], None
    return None, None, None, None


_REMOTE = re.compile(r"(fully[- ]remote|100% remote|remote[- ](?:first|position|role|job|work|opportunity)|work from home|\bwfh\b|\(remote\)|location:\s*remote|^remote$|,\s*remote\b)", re.I)
_HYBRID = re.compile(r"\bhybrid\b", re.I)
_ONSITE = re.compile(r"\b(on[- ]?site|in[- ]office|work from office|office[- ]based|on premise)\b", re.I)


def _work_mode(text: str, location: str | None):
    blob = f"{location or ''}\n{text}"
    if _HYBRID.search(blob):
        return "hybrid", None
    remote, onsite = bool(_REMOTE.search(blob) or re.search(r"\bremote\b", location or "", re.I)), bool(_ONSITE.search(blob))
    if remote and onsite:
        return None, "Both remote and on-site are mentioned; work mode is ambiguous."
    if remote:
        return "remote", None
    if onsite:
        return "onsite", None
    return None, None


def _clean_locations(value: str | None) -> list[str]:
    if not value:
        return []
    out = []
    value = re.sub(r"\([^)]*\)", " ", value)  # "(Hybrid)", "(3 days in office)"
    for part in re.split(r"\s*(?:\||;|/|\bor\b)\s*", value):
        part = re.sub(r"\b(remote|hybrid|on[- ]?site|in[- ]office|only)\b", "", part, flags=re.I)
        part = re.sub(r"[()\-–—]+", " ", part)
        part = re.sub(r"\s+", " ", part).strip(" ,.")
        if part and part.lower() not in ("india only",) and part not in out:
            out.append(part)
    return out


# --------------------------------------------------------------------------- main entry
def parse_jd(
    job: JobInput,
    catalog: SkillCatalog | None = None,
    *,
    ai_client: ClaudeClient | None = None,
    bands: dict[str, float] | None = None,
) -> JobAnalysis:
    catalog = catalog or get_catalog()
    bands = bands or DEFAULT_BANDS
    text = job.job_description.replace("\r\n", "\n").replace("\r", "\n")
    text = text.translate(_TYPOGRAPHY)                      # curly quotes/apostrophes ("What you’ll do") must match the plain headings
    warnings: list[str] = []
    tagged = tag_lines(text)
    if not any(sec in ("required", "preferred", "responsibilities") for _, sec in tagged):
        warnings.append("No requirements/responsibilities sections were detected; classification relies on wording only.")
    if len(text.split()) < 40:
        warnings.append("The job description is very short; extraction may be incomplete.")

    # ---- title
    title = job.job_title
    if not title:
        first = next((l for l, _ in tagged[:1]), "")
        if first and len(first) <= 80 and _TITLE_WORDS.search(first) and not first.endswith((".", ":")):
            title = re.split(r"\s+[-–—|@]\s+", first)[0].strip()
            warnings.append("Job title was taken from the first line of the description.")

    # ---- skills ------------------------------------------------------------------------
    records: dict[str, dict] = {}
    group_counter = 0
    company_key = skill_key(job.company_name or "")

    def own_product(name: str) -> bool:
        k = skill_key(name)
        return len(company_key) >= 4 and bool(k) and (k == company_key or (len(k) >= 5 and (k.startswith(company_key) or company_key.startswith(k))))

    def add(canon: str, written: str, importance: str, evidence: str, group: int | None):
        if importance == "mandatory" and own_product(canon):
            # The hiring company's own product ("Databricks", "Elasticsearch"): learnable on the job, never an entry requirement.
            importance, group = "preferred", None
            note = f"'{canon}' is the hiring company's own product; treated as preferred, not a prerequisite."
            if note not in warnings:
                warnings.append(note)
        rec = records.get(canon)
        if rec is None:
            records[canon] = {"importance": importance, "evidence": evidence, "terms": [written], "group": group}
            return
        if written not in rec["terms"]:
            rec["terms"].append(written)
        if _RANK[importance] > _RANK[rec["importance"]]:
            rec.update(importance=importance, evidence=evidence, group=group)
        elif importance == "mandatory" and rec["importance"] == "mandatory" and group is None:
            rec["group"] = None  # also required on its own

    exp_general, exp_skill = [], []
    required_exp, preferred_exp = [], []
    responsibilities: list[str] = []
    resp_fallback: list[str] = []
    edu: list[EducationRequirement] = []
    certs: list[CertificationRequirement] = []
    auth_req = None
    other_reqs: list[str] = []

    for line, section in tagged:
        for sentence in split_sentences(line):
            importance = _importance(sentence, section)
            if importance == "mandatory" and _CHOOSE.search(sentence):
                importance = "preferred"
            ev = sentence[:200]
            is_degree = bool(_DEGREE_WORDS.search(sentence))

            # skills
            if importance is not None:
                ex = _EXAMPLE.search(sentence)
                regular: list[SkillMention] = []
                for m in _skill_mentions(sentence, catalog):
                    if is_degree and catalog.category(m.canonical) == "domain_knowledge":
                        continue                                          # "degree in Statistics": a field of study, not a skill
                    before = sentence[:m.start]
                    in_paren = before.count("(") > before.count(")") and not _EXPLICIT.search(sentence[before.rfind("("):m.start])
                    purpose = re.search(r"\bfor\s+$", before) is not None and catalog.category(m.canonical) == "domain_knowledge"
                    if (ex and m.start > ex.start()) or in_paren:
                        add(m.canonical, m.written, "example", ev, None)  # "such as AWS" / "(PyTorch, MLflow)" is not a requirement
                    elif purpose:
                        add(m.canonical, m.written, "mentioned", ev, None)  # "Python for statistical modeling": what Python is used for
                    else:
                        regular.append(m)
                runs: list[list[SkillMention]] = []
                for m in regular:  # consecutive mentions joined by ",", "and", "or", "/"
                    if runs and _connector(sentence[runs[-1][-1].end:m.start]) is not None:
                        runs[-1].append(m)
                    else:
                        runs.append([m])
                for r in runs:
                    gids: dict[int, int] = {}
                    if importance == "mandatory" and len(r) >= 2:
                        conns = [(_connector(sentence[a.end:b.start]), sentence[a.end:b.start])
                                 for a, b in zip(r, r[1:])]
                        # "Python and FastAPI, Flask or Django": only the part after the last explicit "and"
                        # is an any-of group. A trailing "or" is required to form a group.
                        last_and = max((i for i, (c, raw) in enumerate(conns)
                                        if c == "and" and re.search(r"\band\b|&", raw, re.I)), default=-1)
                        tail = list(range(last_and + 1, len(r)))
                        if len(tail) >= 2 and conns[-1][0] == "or":
                            group_counter += 1
                            gids = {i: group_counter for i in tail}
                    for i, m in enumerate(r):
                        add(m.canonical, m.written, importance, ev, gids.get(i))

            # experience statements
            for kind, lo, hi, restrictive, skill in ([] if _ORG_YEARS.search(sentence) else _parse_experience(sentence, catalog)):
                is_pref = importance == "preferred"
                (preferred_exp if is_pref else required_exp).append(sentence[:240])
                if kind == "max":
                    exp_general.append(("max", None, hi, True, None, is_pref, False))
                elif skill:
                    exp_skill.append(SkillExperience(skill=skill, years=lo))
                else:
                    exp_general.append(("general", lo, hi, False, None, is_pref, bool(_ALT.search(sentence))))
            if _FRESHER.search(sentence):
                exp_general.append(("general", 0.0, None, False, None, False, False))

            # education
            levels = [(name, rank) for name, rank, rx in _EDU_PATTERNS if rx.search(sentence)]
            if not levels and _EDU_GENERIC[2].search(sentence):
                levels = [(_EDU_GENERIC[0], _EDU_GENERIC[1])]
            if levels and not re.search(r"\b(years?|yrs?)\b.{0,15}\bexperience\b", sentence, re.I) or (levels and _EQUIV.search(sentence)):
                name, rank = min(levels, key=lambda x: x[1])
                fm = _FIELD.search(sentence)
                field = re.sub(r"\s+(or|and)\s*$", "", fm.group(1)).strip() if fm else None
                mandatory = not (importance == "preferred" or _PREF.search(sentence))
                req = EducationRequirement(level=name, level_rank=rank, field=field, mandatory=mandatory,
                                           equivalent_experience_accepted=bool(_EQUIV.search(sentence)), text=sentence[:240])
                if not any(e.level == req.level and e.mandatory == req.mandatory for e in edu):
                    edu.append(req)

            # certifications
            names = [m.group(0).strip() for m in _CERT.finditer(sentence)] + [m.group(0) for m in _CERT_TOKENS.finditer(sentence)]
            for n in names:
                if n.split()[0].lower() in _CERT_LABEL_WORDS:      # "Mandatory Certification(s):" is a label, not a certification
                    continue
                if not any(skill_key(n) == skill_key(c.name) for c in certs):
                    mandatory = not (importance == "preferred" or _PREF.search(sentence) or _POSTHIRE.search(sentence) or own_product(n.split()[0]))
                    certs.append(CertificationRequirement(name=n, mandatory=mandatory, text=sentence[:240]))

            # work authorisation / other hard requirements
            if auth_req is None and _AUTH.search(sentence) and not re.search(r"sponsorship (?:is )?(?:available|provided)|we (?:can )?sponsor", sentence, re.I):
                auth_req = sentence[:240]
            if _OTHER_REQ.search(sentence) and sentence[:200] not in other_reqs and len(other_reqs) < 5:
                other_reqs.append(sentence[:200])

            # responsibilities
            if section == "responsibilities" and len(sentence) >= 15:
                responsibilities.append(sentence.rstrip(".;"))
            elif section in ("general",) and _VERBS.match(sentence) and len(sentence) >= 15 and not _STRONG.search(sentence):
                resp_fallback.append(sentence.rstrip(".;"))

    if not responsibilities:
        responsibilities = resp_fallback
    responsibilities = list(dict.fromkeys(responsibilities))[:25]

    # job-title skills are core to the role -> mandatory (evidence: the title)
    for m in catalog.find_in_text(title or ""):
        add(m.canonical, m.written, "mandatory", f"job title: {title}", None)

    # ---- experience -----------------------------------------------------------------------
    min_exp = max_exp = None
    restrictive = False
    if job.experience_requirement:
        for kind, lo, hi, r, _s in _parse_experience(job.experience_requirement, catalog):
            if kind == "max":
                max_exp, restrictive = hi, True
            else:
                min_exp = lo if min_exp is None else min_exp
                max_exp = hi if hi is not None and max_exp is None else max_exp
        if _FRESHER.search(job.experience_requirement) and min_exp is None:
            min_exp = 0.0
        if min_exp is None and max_exp is None:
            warnings.append("experience_requirement input could not be parsed.")
    if min_exp is None:
        gens = [g for g in exp_general if g[0] == "general" and not g[5]]
        if gens:
            if len({g[1] for g in gens}) > 1:
                warnings.append("Multiple experience requirements found; the first one is used.")
            min_exp, max_exp = gens[0][1], gens[0][2]
            alts = [g for g in gens if g[6]]
            if alts:                                    # "12+ yrs for M.S. holders / 8+ for Ph.D. holders", "2+ years (alternatively 5+)": the lowest way in
                best = min(alts + [gens[0]], key=lambda g: g[1])
                min_exp, max_exp = best[1], best[2]
                warnings.append("The job offers alternative experience routes; the lowest is used.")
    for g in exp_general:
        if g[0] == "max" and not g[5]:
            max_exp, restrictive = g[2], True
    if min_exp is None and exp_skill:
        min_exp = max(e.years for e in exp_skill)
        warnings.append("Minimum experience was derived from a skill-specific requirement.")
    if min_exp is not None and max_exp is not None and max_exp < min_exp:
        max_exp = None
    skill_years: dict[str, float] = {}
    for e in exp_skill:
        skill_years[e.skill] = max(e.years, skill_years.get(e.skill, 0))

    # ---- location, mode, type, salary, seniority ---------------------------------------------
    loc_value = job.location
    if not loc_value:
        for line, _ in tagged:
            m = re.match(r"^(?:job |work |office )?locations?\s*[:\-]\s*(.+)$", line, re.I) or \
                re.match(r"^(?:based|located) (?:in|at)\s+(.+)$", line, re.I)
            if m:
                loc_value = m.group(1)
                break
    work_mode, mode_warn = _work_mode(text, loc_value)
    if mode_warn:
        warnings.append(mode_warn)
    employment = _norm_emp(job.employment_type) or _norm_emp(text[:3000])
    smin, smax, cur, sal_warn = parse_salary(job.salary) if job.salary else (None, None, None, None)
    if smin is None:
        smin, smax, cur, sal_warn2 = parse_salary(text)
        sal_warn = sal_warn or sal_warn2
    if sal_warn:
        warnings.append(sal_warn)

    seniority, source = None, None
    for rx, level in _TITLE_LEVEL:
        if title and rx.search(title):
            seniority, source = level, "title"
            break
    if seniority is None and min_exp is not None:
        seniority, source = _level_from_years(min_exp, bands), "experience_requirement"

    analysis = JobAnalysis(
        job_title=title, company_name=job.company_name,
        location=_clean_locations(loc_value), employment_type=employment, work_mode=work_mode,
        minimum_experience_years=min_exp, maximum_experience_years=max_exp, maximum_is_restrictive=restrictive,
        minimum_salary=smin, maximum_salary=smax, currency=cur,
        education_requirements=edu, certification_requirements=certs,
        skills=[SkillRequirement(name=c, category=catalog.category(c), importance=r["importance"],
                                 original_terms=r["terms"], evidence=r["evidence"], group=r["group"])
                for c, r in records.items()],
        responsibilities=responsibilities,
        required_experience=list(dict.fromkeys(required_exp))[:10],
        preferred_experience=list(dict.fromkeys(preferred_exp))[:10],
        skill_experience=[SkillExperience(skill=k, years=v) for k, v in skill_years.items()],
        seniority_level=seniority, seniority_source=source,
        work_authorization_requirement=auth_req, other_requirements=other_reqs,
        warnings=warnings, jd_text_sha256=hashlib.sha256(text.encode()).hexdigest(),
    )
    if not analysis.skills:
        analysis.warnings.append("No technical skills could be identified in this job description.")
    analysis.finalize()

    if ai_client is not None:
        analysis = _refine_with_ai(analysis, text, job, catalog, ai_client)
    return analysis


# --------------------------------------------------------------------------- optional Claude refinement
def _grounded(skill: str, text_key_space: str, text_lower: str) -> bool:
    k = skill_key(skill)
    return bool(k) and (k in text_key_space or skill.lower() in text_lower)


def _refine_with_ai(analysis: JobAnalysis, text: str, job: JobInput, catalog: SkillCatalog, ai: ClaudeClient) -> JobAnalysis:
    """Let Claude re-classify skills; keep only what is grounded in the JD text."""
    try:
        extraction = ai.complete_json(
            load_prompt("job_analysis"),
            f"Job title: {job.job_title or 'unknown'}\n<job_description>\n{text[:30000]}\n</job_description>",
            AIJobExtraction,
        )
    except (AIUnavailable, AIResponseError) as exc:
        analysis.warnings.append(f"Claude analysis unavailable ({type(exc).__name__}); deterministic parse only.")
        return analysis

    lower, keyspace = text.lower(), skill_key(text)
    by_name = {s.name: s for s in analysis.skills}
    changed = False
    for s in extraction.skills:
        name = catalog.normalize_or_raw(s.name)
        if not _grounded(name, keyspace, lower) and not _grounded(s.name, keyspace, lower):
            analysis.warnings.append(f"Ignored AI skill '{s.name[:40]}': not found in the job description.")
            continue
        cur = by_name.get(name)
        if cur is None:
            analysis.skills.append(SkillRequirement(name=name, category=catalog.category(name), importance=s.importance,
                                                    original_terms=[s.name], evidence=(s.evidence or "")[:200] or None))
            changed = True
        elif cur.importance != s.importance:
            cur.importance = s.importance
            cur.evidence = (s.evidence or cur.evidence or "")[:200] or None
            cur.group = cur.group if s.importance == "mandatory" else None
            changed = True
    if analysis.minimum_experience_years is None and extraction.minimum_experience_years is not None \
            and re.search(rf"\b{int(extraction.minimum_experience_years)}\b", text):
        analysis.minimum_experience_years = extraction.minimum_experience_years
        changed = True
    if not analysis.responsibilities:
        tokens = set(re.findall(r"[a-z]{4,}", lower))
        for r in extraction.responsibilities[:25]:
            rt = set(re.findall(r"[a-z]{4,}", r.lower()))
            if rt and len(rt & tokens) / len(rt) >= 0.7:
                analysis.responsibilities.append(r)
                changed = True
    analysis.finalize()
    analysis.parser = "claude+deterministic"
    return analysis
