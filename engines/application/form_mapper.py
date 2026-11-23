"""Maps application-form fields to VERIFIED candidate information. Never invents anything.

* Values come only from: the verified profile, the approved screening answers (Phase 5), the approved cover letter text, and values the
  candidate typed in for this application (``candidate_inputs``, keyed by field id).
* Every mapping records its source. Derived values (first/last name split, city from a location string, experience years computed from dates)
  are flagged ``derived`` so the candidate reviews them.
* ``confidence`` is rule-based match QUALITY: high = field type or an anchored label matched a known field; medium = a looser label/name/
  placeholder match; low = a weak guess (never auto-filled - becomes ``needs_input``). It is not a probability.
* Sensitive / legally significant fields (demographics, consent, visa sponsorship, salary, work authorisation ...) are only filled from
  explicit candidate input; unknown required questions are ``needs_input``; unknown optional ones are left blank (``optional_skipped``).
* Dropdowns / radios: an option is chosen only when it equals the value (normalised) or an unambiguous yes/no - never a guess.
"""
import re
from dataclasses import dataclass, field

from ai.schemas.candidate_profile import CandidateProfile
from browser.locator_engine import FormField
from database.models.application import FieldMapping

# --------------------------------------------------------------------------------------------------------------- normalising
_NOISE = re.compile(r"[\*\(\)\[\]:?!.,/\\|_-]+")


def normalize(text: str | None) -> str:
    t = _NOISE.sub(" ", (text or "").lower())
    return re.sub(r"\s+", " ", t).strip()


_STOP = {"the", "a", "an", "of", "to", "do", "you", "your", "are", "is", "in", "for", "and", "or", "with", "this", "our", "us", "we", "please",
         "what", "why", "how", "tell", "describe", "have", "has", "can", "will", "would", "be", "on", "at", "it", "that", "any", "if", "as"}


def tokens(text: str) -> set[str]:
    return {t for t in normalize(text).split() if t not in _STOP and len(t) > 2}


# --------------------------------------------------------------------------------------------------------------- the field table
@dataclass(frozen=True)
class Rule:
    canonical: str
    pattern: re.Pattern
    sensitive: bool = False
    kinds: frozenset = frozenset()          # restrict to these field kinds (empty = any)


def R(canonical, pattern, sensitive=False, kinds=()):
    return Rule(canonical, re.compile(pattern, re.I), sensitive, frozenset(kinds))


RULES: list[Rule] = [
    # consent / legal - never ticked on the candidate's behalf
    R("consent", r"\bi (agree|consent|acknowledge|accept|certify|declare|confirm|have read|understand)\b|^(agree|consent|accept)\b|terms( and conditions| of use)?|privacy (policy|notice)|data processing|gdpr|certif\w+ that", True, kinds=("checkbox", "radio")),
    # demographics / protected characteristics - the candidate decides
    R("demographic", r"\b(gender|sex|race|ethnic\w*|veteran|disabilit\w*|pronouns?|sexual orientation|religio\w*|marital|date of birth|dob|age|nationality|caste|citizenship status)\b", True),
    R("sponsorship", r"sponsor\w*|visa( status)?|require .*(work )?(permit|visa)", True),
    R("work_authorization", r"authori[sz]ed to work|work authori[sz]ation|legally (authori[sz]ed|eligible|entitled)|right to work|eligible to work|work permit", True),
    R("salary_expectation", r"salary|compensation|\bctc\b|expected (pay|package)|pay expectation|remuneration", True),
    R("notice_period", r"notice period|how soon can you|earliest (start|join)|available to (start|join)|when can you (start|join)|joining time|availability to (start|join)"),
    R("relocation", r"relocat\w*|willing to move|open to moving"),
    R("first_name", r"^(legal |given |your )?first name$|^given name$|^fname$|^first$"),
    R("last_name", r"^(legal |your )?(last|family) name$|^surname$|^lname$|^last$"),
    R("full_name", r"^(your |legal |candidate )?(full )?name$|^full name$|^name$|candidate name"),
    R("email", r"e ?mail", kinds=("email", "text")),
    R("phone", r"phone|mobile|telephone|contact (number|no)|cell", kinds=("tel", "text")),
    R("linkedin_url", r"linkedin"),
    R("github_url", r"github"),
    R("portfolio_url", r"portfolio|personal (web ?site|site|page)|^web ?site$|^website url$|^url$|other (link|url)"),
    R("current_company", r"current (company|employer|organi[sz]ation)|present (company|employer)|^employer$|^company$|^organi[sz]ation$"),
    R("current_title", r"current (job |work )?(title|role|position|designation)|present (title|role|position)|^job title$|^title$|^designation$|^position$"),
    R("years_experience", r"years? of (total |relevant |professional |work )?experience|total experience|experience in years|how many years|\(years\)"),
    R("location", r"current location|^location$|^city$|where are you (based|located)|city of residence|^address$|place of residence"),
    R("country", r"^country$|country of residence|^country/region$"),
    R("skills", r"technical skills|key skills|^skills$|core skills|skill set|areas of expertise|technologies"),
    R("work_history", r"work (experience|history)|employment history|professional experience|relevant experience|experience summary"),
    R("education_degree", r"highest (level of )?(education|qualification|degree)|^degree$|^qualification$"),
    R("education_institution", r"university|college|institution|school"),
    R("cover_letter_text", r"cover letter|covering letter|letter of motivation|motivation letter"),
    R("resume_upload", r"resume|r[eé]sum[eé]|\bcv\b|curriculum vitae", kinds=("file",)),
]
# questions a screening answer can be matched to by id (Phase 5 ids) -> patterns on the question text
SCREENING_HINTS = {
    "tell_me_about_yourself": r"tell (us|me) about yourself|about yourself|introduce yourself|brief (bio|introduction)|short (bio|introduction)",
    "why_company": r"why (do you want to|would you like to|are you interested in) (join|work)|why (this|our) company|why us\b|interest in (this|our) company",
    "why_suitable": r"why (are you|would you be) (suitable|a (good )?fit|right)|why should we (hire|consider) you|what makes you (suitable|a good fit)",
    "relevant_experience": r"(describe|summari[sz]e|tell us about) your (relevant )?experience|relevant experience",
    "strongest_skills": r"(strongest|top|key|main) (technical )?skills|what are you (best|strongest) at",
    "challenging_project": r"challenging (project|problem)|(describe|tell us about) a (difficult|challenging|complex) (project|problem)",
}
SCREENING_MATCH_THRESHOLD = 0.6          # token overlap (Jaccard) needed to reuse an approved answer for a differently worded question
YES = re.compile(r"^\s*(yes|y|true|i am|i do|willing|open)\b", re.I)
NO = re.compile(r"^\s*(no|n|false|i am not|i do not|not willing|unwilling)\b", re.I)


# --------------------------------------------------------------------------------------------------------------- facts
@dataclass
class Facts:
    """Everything the mapper may use. Built from verified/approved data only."""
    profile: CandidateProfile
    answers: dict[str, dict] = field(default_factory=dict)        # approved screening answers by question_id: {question, answer, status}
    cover_letter_text: str | None = None
    candidate_inputs: dict[str, str | bool] = field(default_factory=dict)   # field_id | canonical -> value typed by the candidate for THIS application
    personal: dict[str, str] = field(default_factory=dict)        # notice_period / salary_expectation / relocation / work_authorization (preferences or inputs)


def all_skills(p: CandidateProfile) -> list[str]:
    seen, out = set(), []
    for group in p.technical_skills.model_dump().values():
        for s in group:
            if s and s.lower() not in seen:
                seen.add(s.lower())
                out.append(s)
    return out


def current_job(p: CandidateProfile):
    for w in p.work_experience:
        if w.is_current:
            return w
    return None


def work_history_text(p: CandidateProfile) -> str | None:
    lines = []
    for w in p.work_experience:
        if not (w.job_title or w.company_name):
            continue
        period = f" ({w.start_date or '?'} - {'present' if w.is_current else (w.end_date or '?')})"
        lines.append(f"{w.job_title or ''}{' at ' if w.job_title and w.company_name else ''}{w.company_name or ''}{period}")
    return "\n".join(lines) or None


# --------------------------------------------------------------------------------------------------------------- classification
def _texts(f: FormField) -> list[tuple[str, str]]:
    """(source, normalised text) candidates in order of reliability: label, group, aria-ish placeholder, name."""
    return [(k, normalize(v)) for k, v in (("label", f.label), ("group", f.group), ("placeholder", f.placeholder), ("name", f.name.replace("_", " ").replace("-", " "))) if v]


def classify(f: FormField) -> tuple[str | None, str, bool]:
    """-> (canonical, confidence, sensitive). Unknown -> (None, 'none', False)."""
    if f.kind == "file":
        label = normalize(" ".join([f.label, f.name, f.placeholder]))
        if re.search(r"cover letter|covering letter", label):
            return "cover_letter_upload", "high", False
        if re.search(r"resume|r[eé]sum[eé]|\bcv\b|curriculum vitae", label):
            return "resume_upload", "high", False
        return None, "none", False
    for rule in RULES:
        if rule.canonical == "resume_upload":
            continue
        if rule.canonical == "consent" and f.kind not in rule.kinds:
            continue  # only tick-boxes/radios are consents; "Confirm email" is not
        if rule.kinds and f.kind not in rule.kinds and f.kind not in ("text", "textarea"):
            continue
        for source, text in _texts(f):
            if rule.pattern.search(text):
                anchored = source in ("label", "group") or f.kind in ("email", "tel")
                conf = "high" if (anchored and (f.kind in ("email", "tel") or len(text.split()) <= 6)) else "medium" if anchored else "low"
                return rule.canonical, conf, rule.sensitive
    if f.kind == "email":
        return "email", "high", False
    if f.kind == "tel":
        return "phone", "high", False
    return None, "none", False


def match_screening(label: str, answers: dict[str, dict]) -> tuple[str | None, str]:
    """Find the approved answer for a free-text question. -> (question_id, 'high'|'medium'|'none')."""
    norm = normalize(label)
    if not norm:
        return None, "none"
    for qid, a in answers.items():
        if normalize(a.get("question")) == norm:
            return qid, "high"
    for qid, pattern in SCREENING_HINTS.items():
        if qid in answers and re.search(pattern, norm, re.I):
            return qid, "medium"
    q = tokens(label)
    best, score = None, 0.0
    for qid, a in answers.items():
        t = tokens(a.get("question", ""))
        if q and t:
            j = len(q & t) / len(q | t)
            if j > score:
                best, score = qid, j
    return (best, "medium") if best and score >= SCREENING_MATCH_THRESHOLD else (None, "none")


# --------------------------------------------------------------------------------------------------------------- value choice
def _pick_option(options: list[str], value: str) -> str | None:
    """An option that equals the value (normalised) - or a unique one containing it. Never a guess."""
    nv = normalize(value)
    if not nv:
        return None
    exact = [o for o in options if normalize(o) == nv]
    if len(exact) == 1:
        return exact[0]
    part = [o for o in options if nv in normalize(o) or (normalize(o) and normalize(o) in nv)]
    part = [o for o in part if normalize(o) not in ("select", "choose", "please select", "")]
    return part[0] if len(part) == 1 else None


def _yes_no(options: list[str], answer: str) -> str | None:
    want = YES if YES.match(answer or "") else NO if NO.match(answer or "") else None
    if want is None:
        return None
    hits = [o for o in options if want.match(o or "")]
    return hits[0] if len(hits) == 1 else None


def _profile_value(canonical: str, f: FormField, facts: Facts) -> tuple[str | None, str | None, bool, str | None]:
    """-> (value, source, derived, note)."""
    p = facts.profile
    c = p.contact
    job = current_job(p)
    if canonical == "full_name":
        return c.full_name, "contact.full_name", False, None
    if canonical in ("first_name", "last_name") and c.full_name:
        parts = c.full_name.split()
        if len(parts) >= 2:
            return (parts[0] if canonical == "first_name" else " ".join(parts[1:])), "contact.full_name", True, "Split from your full name - check it."
        return None, None, False, "Your name has one part; enter it yourself."
    if canonical == "email":
        return c.email, "contact.email", False, None
    if canonical == "phone":
        return c.phone, "contact.phone", False, None
    if canonical == "linkedin_url":
        return c.linkedin_url, "contact.linkedin_url", False, None
    if canonical == "github_url":
        return c.github_url, "contact.github_url", False, None
    if canonical == "portfolio_url":
        return c.portfolio_url, "contact.portfolio_url", False, None
    if canonical == "location" and c.location:
        if normalize(f.label) in ("city",):
            return c.location.split(",")[0].strip(), "contact.location", True, "City taken from your location - check it."
        return c.location, "contact.location", False, None
    if canonical == "current_company":
        return (job.company_name, "work_experience.current.company_name", False, None) if job and job.company_name else (None, None, False, "No current employer in your profile.")
    if canonical == "current_title":
        if job and job.job_title:
            return job.job_title, "work_experience.current.job_title", False, None
        if p.summary.current_job_title:
            return p.summary.current_job_title, "summary.current_job_title", False, None
        return None, None, False, "No current job title in your profile."
    if canonical == "years_experience" and p.summary.total_experience_years is not None:
        y = p.summary.total_experience_years
        prov = p.provenance.get("summary.total_experience_years")
        derived = bool(prov and prov.kind == "inferred")
        return (f"{y:g}", "summary.total_experience_years", derived, "Calculated from your employment dates - check it." if derived else None)
    if canonical == "skills":
        s = all_skills(p)
        return (", ".join(s[:40]), "technical_skills", False, None) if s else (None, None, False, None)
    if canonical == "work_history":
        t = work_history_text(p)
        return (t, "work_experience", False, None) if t else (None, None, False, None)
    if canonical == "education_degree" and p.education and p.education[0].degree:
        e = p.education[0]
        return " ".join(x for x in (e.degree, e.specialization) if x), "education[0]", False, None
    if canonical == "education_institution" and p.education and p.education[0].institution:
        return p.education[0].institution, "education[0].institution", False, None
    if canonical == "cover_letter_text" and facts.cover_letter_text:
        return facts.cover_letter_text, "approved cover letter", False, None
    return None, None, False, None


# --------------------------------------------------------------------------------------------------------------- the mapper
def map_field(f: FormField, facts: Facts) -> FieldMapping:
    base = dict(field_id=f.id, label=f.describe(), kind=f.kind, required=f.required, options=list(f.options))
    typed = facts.candidate_inputs.get(normalize(f.label))
    if typed is None:
        typed = facts.candidate_inputs.get(f.id)
    canonical, conf, sensitive = classify(f)

    # 1) the candidate's own entry for this exact field always wins (explicit input is how sensitive/unknown fields are answered)
    if typed is not None and (str(typed).strip() or isinstance(typed, bool)):
        value = typed
        if f.kind in ("select", "radio") and isinstance(typed, str):
            chosen = _pick_option(f.options, typed)
            if chosen is None:
                return FieldMapping(**base, canonical=canonical, value=None, source="candidate_input", confidence="high", status="needs_input", sensitive=sensitive,
                                    note=f"'{typed}' is not one of the available options.")
            value = chosen
        return FieldMapping(**base, canonical=canonical, value=value, source="candidate_input", confidence="high", status="ready", sensitive=sensitive)

    if not f.identified:
        return FieldMapping(**base, canonical=None, status="manual_required", note="This field could not be identified; fill it in yourself.")
    if f.kind == "password":
        return FieldMapping(**base, canonical="password", status="manual_required", sensitive=True, note="Passwords are never entered by this tool.")

    # 2) uploads are handled by the document uploader (approved files only)
    if f.kind == "file":
        if canonical in ("resume_upload", "cover_letter_upload"):
            return FieldMapping(**base, canonical=canonical, source="approved document", confidence=conf, status="ready" if f.required or canonical == "resume_upload" else "ready")
        return FieldMapping(**base, canonical=None, status="manual_required" if f.required else "optional_skipped",
                            note="Only your approved resume and cover letter are uploaded; attach other files yourself.")

    # 3) sensitive / legally significant: only from explicit input or the candidate's saved answers (never inferred)
    if canonical in ("consent", "demographic", "sponsorship"):
        status = "manual_required" if f.required else "optional_skipped"
        return FieldMapping(**base, canonical=canonical, confidence=conf, status=status, sensitive=True,
                            note="This is a sensitive/legal question. Answer it yourself (add your answer for this field); it is never filled automatically.")
    if canonical in ("work_authorization", "salary_expectation", "notice_period", "relocation"):
        val = (facts.personal.get(canonical) or "").strip()
        if canonical in ("work_authorization", "salary_expectation"):
            sensitive = True
        if conf == "low":
            return FieldMapping(**base, canonical=canonical, confidence="low", status="needs_input" if f.required else "optional_skipped", sensitive=sensitive,
                                note="Not sure what this field asks; enter it yourself.")
        if not val:
            return FieldMapping(**base, canonical=canonical, confidence=conf, status="needs_input" if f.required else "optional_skipped", sensitive=sensitive,
                                source=None, note="Not in your profile/preferences; never guessed. Provide it for this application.")
        return _finish_choice(f, base, canonical, val, f"personal.{canonical}", conf, sensitive, False)

    # 4) profile facts
    if canonical and canonical not in ("resume_upload",):
        if conf == "low":
            return FieldMapping(**base, canonical=canonical, confidence="low", status="needs_input" if f.required else "optional_skipped",
                                note="Weak match for this field; confirm or enter the value yourself.")
        value, source, derived, note = _profile_value(canonical, f, facts)
        if value:
            return _finish_choice(f, base, canonical, value, source, conf, sensitive, derived, note)
        return FieldMapping(**base, canonical=canonical, confidence=conf, status="needs_input" if f.required else "optional_skipped",
                            note=note or "Not available in your verified profile; enter it yourself.")

    # 5) free-text question -> an approved screening answer
    qtext = f.label or f.group or f.placeholder
    qid, qconf = match_screening(qtext, facts.answers)
    if qid:
        a = facts.answers[qid]
        if f.kind in ("text", "textarea"):
            return FieldMapping(**base, canonical="screening_question", value=a["answer"], source=f"screening_answers.{qid}", confidence=qconf, status="ready",
                                note=("Reworded question matched to your approved answer - check it." if qconf != "high" else None))
        return _finish_choice(f, base, "screening_question", a["answer"], f"screening_answers.{qid}", qconf, False, False)

    # 6) unknown
    if f.kind in ("checkbox",) and not f.required:
        return FieldMapping(**base, canonical=None, status="optional_skipped")
    return FieldMapping(**base, canonical=None, status="needs_input" if f.required else "optional_skipped",
                        note="Unknown question - answer it yourself; nothing is guessed." if f.required else "Optional and not recognised; left blank.")


def _finish_choice(f: FormField, base: dict, canonical: str, value: str, source: str | None, conf: str, sensitive: bool, derived: bool, note: str | None = None) -> FieldMapping:
    """Turn a chosen value into a mapping, resolving dropdown / radio / checkbox options without guessing."""
    if f.kind in ("select", "radio"):
        chosen = _yes_no(f.options, value) if canonical in ("relocation", "work_authorization", "screening_question") else None
        chosen = chosen or _pick_option(f.options, value)
        if chosen is None:
            return FieldMapping(**base, canonical=canonical, source=source, confidence=conf, status="needs_input", sensitive=sensitive,
                                note="Your saved value does not match one of the dropdown/radio options; choose it yourself.")
        return FieldMapping(**base, canonical=canonical, value=chosen, source=source, confidence=conf, status="ready", sensitive=sensitive, derived=derived, note=note)
    if f.kind == "checkbox":
        yn = YES.match(value or "") is not None
        return FieldMapping(**base, canonical=canonical, value=yn, source=source, confidence=conf, status="ready", sensitive=sensitive, derived=derived, note=note)
    return FieldMapping(**base, canonical=canonical, value=value, source=source, confidence=conf, status="ready", sensitive=sensitive, derived=derived, note=note)


def map_form(fields: list[FormField], facts: Facts) -> list[FieldMapping]:
    return [map_field(f, facts) for f in fields]


def missing_labels(mapping: list[FieldMapping]) -> list[str]:
    """What the candidate still has to provide before this can be submitted."""
    return [m.label for m in mapping if m.required and m.status in ("needs_input", "manual_required")]
