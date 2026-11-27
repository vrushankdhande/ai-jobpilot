"""Skills a job site LISTS for a job (e.g. the skill chips in an Instahyre alert), compared with the candidate's skills.

Information only, never a match score: a short list of tags is not a job description (no must-haves, no experience, no context). It uses
the same skill catalogue and the same exact/related rules as the matching engine, so "you have it" means the same thing in both places.
Skills the catalogue does not know are listed as "not checked" - never counted as missing or as present.
"""
from engines.matching.skill_extractor import SkillCatalog


def compare_listed_skills(listed: list[str], candidate: set[str], catalog: SkillCatalog) -> dict:
    have: list[str] = []
    related: list[dict] = []
    missing: list[str] = []
    unchecked: list[str] = []
    seen: set[str] = set()
    for raw in listed or []:
        text = (raw or "").strip()
        if not text:
            continue
        canon = catalog.list_item_skill(text) or catalog.normalize(text)
        key = (canon or text).lower()
        if key in seen:
            continue
        seen.add(key)
        if not canon:
            unchecked.append(text)
            continue
        m = catalog.match(canon, candidate)
        if m.kind == "exact":
            have.append(canon)
        elif m.kind == "related":
            related.append({"skill": canon, "via": m.candidate_skill})
        else:
            missing.append(canon)
    return {"have": have, "related": related, "missing": missing, "not_checked": unchecked,
            "checked": len(have) + len(related) + len(missing)}
