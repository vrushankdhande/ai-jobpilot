"""Which application a rejection e-mail is about. Pure and deterministic.

Rule (chosen by the candidate): an application is marked rejected automatically ONLY when the company AND the role match exactly one
application that was sent. Anything less certain (the role is not named, several roles at the same company, or the application was not
yet recorded as sent) is offered for confirmation; no match at all - or a different role at that company - is listed as unmatched. Nothing is ever guessed.
"""
import re
from dataclasses import dataclass, field

from integrations.email.rejection_mail import RejectionSignal

AUTO_STATES = {"SUBMITTED", "VERIFIED", "VERIFICATION_PENDING"}     # the application was sent (or may have been)
_LEGAL = {"pvt", "private", "ltd", "limited", "inc", "incorporated", "llc", "llp", "plc", "corp", "corporation", "co", "company", "gmbh", "sa", "ag", "bv", "the"}
_ROLE_STOP = {"the", "a", "an", "and", "of", "for", "in", "at", "to", "with", "remote", "hybrid", "onsite", "on", "site", "wfh", "india", "full", "time", "fulltime",
              "permanent", "contract", "m", "f", "d", "all", "genders"}


def _words(s: str | None) -> list[str]:
    return re.sub(r"[^a-z0-9+#]+", " ", re.sub(r"\(.*?\)", " ", (s or "").lower())).split()


def company_core(name: str | None) -> str:
    """'Acme Analytics Pvt. Ltd.' -> 'acme analytics'."""
    words = _words(name)
    while words and words[-1] in _LEGAL:
        words.pop()
    while words and words[0] == "the":
        words.pop(0)
    return " ".join(words)


def _contains(haystack: str, needle: str) -> bool:
    return bool(needle) and f" {needle} " in f" {haystack} "


def company_match(app_company: str | None, signal: RejectionSignal, mail_words: str) -> int:
    """2 = the company the rejection names (or its sender) is this application's company; 1 = the name only appears in the e-mail text
    (good enough to ASK, never to mark automatically); 0 = no."""
    core = company_core(app_company)
    if not core:
        return 0
    for other in (signal.company, signal.company_from_sender):
        oc = company_core(other)
        if oc and (oc == core or (len(oc) >= 4 and (core.startswith(oc + " ") or oc.startswith(core + " "))) or oc.replace(" ", "") == core.replace(" ", "")):
            return 2
    return 1 if len(core) >= 4 and _contains(mail_words, core) else 0


def _role_tokens(s: str | None) -> set[str]:
    return {w for w in _words(s) if w not in _ROLE_STOP}


def role_matches(app_title: str | None, signal: RejectionSignal, mail_words: str) -> bool:
    title = " ".join(w for w in _words(app_title) if w not in _ROLE_STOP)
    if not title:
        return False
    if len(title) >= 6 and _contains(mail_words, title):            # the exact job title appears in the e-mail
        return True
    a, b = _role_tokens(app_title), _role_tokens(signal.role)
    if not a or not b:
        return False
    if a == b:
        return True
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    return len(small) >= 2 and small <= big and len(small) / len(big) >= 0.6


@dataclass
class MatchResult:
    decision: str                                   # "auto" | "confirm" | "unmatched"
    application_ids: list[str] = field(default_factory=list)
    reason: str = ""


def match_rejection(signal: RejectionSignal, mail_text: str, applications: list[dict]) -> MatchResult:
    """``applications`` = the candidate's open applications (dicts with application_id, company_name, job_title, state)."""
    words = " ".join(_words(mail_text))
    scored = [(company_match(a.get("company_name"), signal, words), a) for a in applications]
    strong = [a for s, a in scored if s == 2]
    by_company = strong or [a for s, a in scored if s == 1]
    if not by_company:
        return MatchResult("unmatched", [], "no application at this company")
    by_role = [a for a in by_company if role_matches(a.get("job_title"), signal, words)]
    if not strong:
        return MatchResult("confirm", [a["application_id"] for a in (by_role or by_company)], "the company is only mentioned in the e-mail")
    if len(by_role) == 1:
        app = by_role[0]
        if app.get("state") in AUTO_STATES:
            return MatchResult("auto", [app["application_id"]], "company and role match one application")
        return MatchResult("confirm", [app["application_id"]], "company and role match, but this application is not recorded as sent")
    if len(by_role) > 1:
        return MatchResult("confirm", [a["application_id"] for a in by_role], "several applications match the company and role")
    if signal.role:      # a DIFFERENT role at this company: most likely an application made outside JobPilot - listed, never offered as a match
        return MatchResult("unmatched", [], f"you have no application for this role at {by_company[0].get('company_name') or 'this company'}")
    return MatchResult("confirm", [a["application_id"] for a in by_company], "the e-mail names no role")
