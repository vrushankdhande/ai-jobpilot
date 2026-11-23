"""Screening-question answers from verified facts.

Personal details that are NOT in the profile or preferences (notice period, salary expectation, relocation, work
authorisation) are never invented: the answer is a placeholder with status ``needs_input`` and approval is blocked until
the candidate supplies it. Motivation answers are drafts built from the role's duties and are flagged for review.
"""
from ai.schemas.document_schema import PLACEHOLDER, ScreeningAnswer, ScreeningContent
from config.scoring_config import Preferences
from engines.generation.cover_letter import _join, cap_first, first_person
from engines.generation.fact_pack import FactPack

DEFAULT_QUESTIONS = [
    ("tell_me_about_yourself", "Tell us about yourself."),
    ("why_company", "Why do you want to join our company?"),
    ("why_suitable", "Why are you suitable for this role?"),
    ("relevant_experience", "Describe your relevant experience."),
    ("strongest_skills", "What are your strongest technical skills?"),
    ("challenging_project", "Describe a challenging project."),
    ("notice_period", "What is your notice period?"),
    ("salary_expectation", "What are your salary expectations?"),
    ("relocation", "Are you willing to relocate?"),
    ("work_authorization", "Are you authorised to work in the country where this role is based?"),
]
PERSONAL_IDS = {"notice_period", "salary_expectation", "relocation", "work_authorization"}
_COUNT = {"short": 1, "medium": 2, "long": 4}


def _need(qid: str, question: str, length: str, what: str) -> ScreeningAnswer:
    return ScreeningAnswer(question_id=qid, question=question, length=length, status="needs_input",
                           answer=f"{PLACEHOLDER}: {what}]", note=f"{what[0].upper() + what[1:]} is not in your profile or preferences.")


def effective_inputs(prefs: Preferences, candidate_inputs: dict | None) -> dict[str, str]:
    """Personal details the candidate provided (request) or recorded (job_preferences.yaml). Nothing else counts."""
    out: dict[str, str] = {}
    if prefs.notice_period:
        out["notice_period"] = prefs.notice_period
    if prefs.min_salary:
        out["salary_expectation"] = f"{(prefs.salary_currency + ' ') if prefs.salary_currency else ''}{prefs.min_salary:,.0f} per year"
    if prefs.open_to_relocation is not None:
        out["relocation"] = "yes" if prefs.open_to_relocation else "no"
    if prefs.work_authorization:
        out["work_authorization"] = ", ".join(prefs.work_authorization)
    for k, v in (candidate_inputs or {}).items():
        if k in PERSONAL_IDS and str(v).strip():
            out[k] = str(v).strip()
    return out


def answer_one(qid: str, question: str, p: FactPack, length: str, inputs: dict[str, str]) -> ScreeningAnswer:
    n = _COUNT[length]
    base = dict(question_id=qid, question=question, length=length)
    roles = [r for r in p.roles if r.bullets]

    if qid == "tell_me_about_yourself":
        parts = []
        if p.title and p.years and p.years >= 1:
            parts.append(f"I am a {p.title} with {int(p.years)}+ years of experience.")
        elif p.title:
            parts.append(f"I am a {p.title}.")
        skills = p.matched_skills[:4] or p.top_skills[:4]
        if skills:
            parts.append(f"My core skills include {_join(skills)}.")
        if length != "short" and roles:
            r = roles[0]
            parts.append(f"Most recently I worked as {r.job_title or 'a team member'}" + (f" at {r.company_name}" if r.company_name else "") + ".")
        if length == "long":
            if p.achievements:
                parts.append(f"A result I am proud of: {p.achievements[0][0].rstrip('.')}.")
            if p.education:
                parts.append(f"I hold a {p.education}.")
        if not parts:
            return _need(qid, question, length, "a short introduction (your profile has no facts to build one from)")
        return ScreeningAnswer(**base, answer=" ".join(parts), sources=["summary", "technical_skills"])

    if qid == "why_company":
        if not (p.company or p.job_title) or not (p.best_responsibility or p.jd_responsibilities):
            return _need(qid, question, length, "your own reasons for wanting to join this company")
        duty = p.best_responsibility or p.jd_responsibilities[0]
        who = p.company or "this company"
        text = f'The {p.job_title or "role"} at {who} appeals to me because its duties include "{duty}"'
        text += f", which is close to my work as {p.title}" if p.title else ""
        text += f" with {_join(p.matched_skills[:3])}." if p.matched_skills else "."
        return ScreeningAnswer(**base, answer=text, status="draft", sources=["job.responsibilities"],
                               note="Built only from the job's duties and your skills. Your personal reasons for joining the company "
                                    "are not in your profile - edit this to say what really attracts you.")

    if qid == "why_suitable":
        if not p.jd_must and not p.matched_skills:
            return _need(qid, question, length, "why you are suitable (the job lists no requirements to compare with)")
        parts = []
        if p.jd_must and p.matched_skills:  # two sentences: what the job asks for is not a claim about me
            parts.append(f"The role asks for {_join(p.jd_must[:4])}.")
            parts.append(f"I have hands-on experience with {_join(p.matched_skills[:4])}.")
        elif p.matched_skills:
            parts.append(f"I have hands-on experience with {_join(p.matched_skills[:4])}.")
        if p.years and p.years >= 1 and p.title:
            parts.append(f"I bring {int(p.years)}+ years of experience as a {p.title}.")
        if length != "short" and roles:
            parts.append(cap_first(first_person(roles[0].bullets[0][0])) + ".")
        if length == "long" and p.achievements:
            parts.append(f"For example: {p.achievements[0][0].rstrip('.')}.")
        return ScreeningAnswer(**base, answer=" ".join(parts), sources=["technical_skills", "work_experience"]) if parts else _need(
            qid, question, length, "why you are suitable")

    if qid == "relevant_experience":
        if not roles:
            return _need(qid, question, length, "your relevant experience (no work history in the profile)")
        parts, srcs = [], []
        for r in roles[: (1 if length == "short" else 2 if length == "medium" else 3)]:
            lead = f"As {r.job_title or 'a team member'}" + (f" at {r.company_name}" if r.company_name else "") + (f" ({r.period})" if r.period else "")
            parts.append(f"{lead}, {first_person(r.bullets[0][0])}.")
            srcs.append(r.bullets[0][1])
            if length == "long" and len(r.bullets) > 1:
                parts.append(cap_first(first_person(r.bullets[1][0])) + ".")
                srcs.append(r.bullets[1][1])
        return ScreeningAnswer(**base, answer=" ".join(parts), sources=srcs)

    if qid == "strongest_skills":
        skills = p.matched_skills[: n + 2] or p.top_skills[: n + 2]
        if not skills:
            return _need(qid, question, length, "your strongest technical skills")
        text = f"My strongest technical skills are {_join(skills)}."
        if length != "short" and roles and p.matched_skills:
            text += f" I use them in my work as {roles[0].job_title or 'a team member'}" + (f" at {roles[0].company_name}." if roles[0].company_name else ".")
        return ScreeningAnswer(**base, answer=text, sources=["technical_skills"])

    if qid == "challenging_project":
        pr = next((x for x in p.projects if x.description or x.bullets), None)
        if pr is None:
            return _need(qid, question, length, "a challenging project (no projects in the profile)")
        text = f"One project I worked on is {pr.name or 'a project of mine'}"
        text += f": {pr.description.rstrip('.')}." if pr.description else "."
        if pr.technologies and length != "short":
            text += f" It used {_join(pr.technologies[:5])}."
        if pr.bullets and length != "short":
            text += " " + cap_first(first_person(pr.bullets[0][0])) + "."
        return ScreeningAnswer(**base, answer=text, status="draft", sources=[f"projects[{pr.index}]"],
                               note="Facts only. Add what made the project challenging - that is not in your profile.")

    if qid == "notice_period":
        v = inputs.get("notice_period")
        return ScreeningAnswer(**base, answer=f"My notice period is {v}.", sources=["candidate_input"]) if v else _need(qid, question, length, "your notice period")

    if qid == "salary_expectation":
        v = inputs.get("salary_expectation")
        return ScreeningAnswer(**base, answer=f"My salary expectation is {v}.", sources=["candidate_input"]) if v else _need(
            qid, question, length, "your salary expectation")

    if qid == "relocation":
        v = inputs.get("relocation")
        if not v:
            return _need(qid, question, length, "whether you are willing to relocate")
        low = v.strip().lower()
        text = "Yes, I am open to relocating." if low in ("yes", "y", "true", "open") else "I am not looking to relocate." if low in ("no", "n", "false") else v
        return ScreeningAnswer(**base, answer=text, sources=["candidate_input"])

    if qid == "work_authorization":
        v = inputs.get("work_authorization")
        return ScreeningAnswer(**base, answer=f"I am authorised to work in {v}.", sources=["candidate_input"]) if v else _need(
            qid, question, length, "your work authorisation")

    return _need(qid, question, length, "your answer to this question")  # custom question: never guessed


def compose_screening(p: FactPack, prefs: Preferences, questions: list[dict] | None = None, length: str = "medium",
                      candidate_inputs: dict | None = None) -> ScreeningContent:
    inputs = effective_inputs(prefs, candidate_inputs)
    chosen = questions or [{"id": i, "text": t} for i, t in DEFAULT_QUESTIONS]
    answers = []
    for n, q in enumerate(chosen):
        qid = q.get("id") or next((i for i, t in DEFAULT_QUESTIONS if t.lower() == q["text"].strip().lower()), None) or f"custom_{n + 1}"
        answers.append(answer_one(qid, q["text"], p, length, inputs))
    return ScreeningContent(answers=answers, candidate_inputs={k: v for k, v in inputs.items()})

