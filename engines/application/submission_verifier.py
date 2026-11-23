"""Decides what happened after the submit button was clicked - from EVIDENCE, never from the click itself.

Outcomes:
* ``confirmed``   - a confirmation message AND the application form is gone (and no validation errors). -> SUBMITTED.
                    With a reference number on the confirmation page (or user/email evidence) the application may become VERIFIED.
* ``failed``      - the site shows validation errors and the form is still there: it was NOT accepted. Safe to fix and prepare again.
* ``unconfirmed`` - anything else (no confirmation, a CAPTCHA appeared, timeout, navigation error, the form simply vanished ...).
                    -> VERIFICATION_PENDING: a human must check; the application is never resubmitted automatically.
"""
import re
from dataclasses import dataclass, field

from database.models.application import SubmissionResult
from ai.schemas.candidate_profile import utcnow


@dataclass
class Verdict:
    outcome: str                                   # confirmed | failed | unconfirmed
    evidence: list[str] = field(default_factory=list)
    reference: str | None = None
    detail: str = ""

    def to_result(self, verified_by: str | None = "page") -> SubmissionResult:
        return SubmissionResult(outcome=self.outcome, evidence=self.evidence, reference=self.reference, detail=self.detail, at=utcnow(),
                                verified_by=verified_by if self.outcome == "confirmed" else None)


_NOT_A_REFERENCE = {"received", "submitted", "number", "sent", "confirmed", "below", "above", "details", "please", "thanks", "thank"}


def extract_reference(text: str, patterns: tuple[str, ...]) -> str | None:
    for pat in patterns:
        for m in re.finditer(pat, text or "", re.I):
            ref = m.group(1).strip(" .,:;")
            # a real reference contains a digit (words like "Application received" must not become a reference)
            if ref.lower() not in _NOT_A_REFERENCE and re.search(r"\d", ref):
                return ref[:40]
    return None


def verify_submission(*, text: str, url: str | None, form_still_present: bool, errors: list[str], adapter, captcha: bool = False,
                      navigation_error: bool = False) -> Verdict:
    ev: list[str] = []
    if navigation_error:
        return Verdict("unconfirmed", ev, None, "The browser failed after the submit button was clicked; the outcome is unknown.")
    if captcha:
        return Verdict("unconfirmed", ev, None, "A CAPTCHA/bot check appeared after submitting; the outcome is unknown. Check yourself.")
    has_msg = any(re.search(p, text or "", re.I) for p in adapter.confirmation_patterns)
    if errors and form_still_present:
        return Verdict("failed", ["validation errors shown"], None, "The site did not accept the application: " + "; ".join(errors[:3]))
    if has_msg and not form_still_present:
        ev += ["confirmation message", "application form no longer shown"]
        if url and any(re.search(p, url, re.I) for p in adapter.confirmation_url_patterns):
            ev.append("confirmation page URL")
        ref = extract_reference(text, adapter.reference_patterns)
        if ref:
            ev.append("reference number")
        return Verdict("confirmed", ev, ref, "")
    if has_msg:
        return Verdict("unconfirmed", ["confirmation-like text (form still shown)"], None,
                       "Confirmation-like text was found but the form is still on the page; not accepted as evidence.")
    if not form_still_present:
        return Verdict("unconfirmed", ["form no longer shown"], None, "The form disappeared but no confirmation was found; check yourself before doing anything else.")
    return Verdict("unconfirmed", [], None, "No confirmation was shown after submitting; the outcome is unknown.")


def can_mark_verified(result: SubmissionResult) -> bool:
    """VERIFIED needs reliable evidence: a page-confirmed submission WITH a reference number, or the candidate's own recorded confirmation
    (reference or confirmation text). A click, a vanished form or a confirmation-like phrase alone is never enough."""
    if result.outcome == "confirmed" and result.verified_by == "page":
        return bool(result.reference) and "confirmation message" in result.evidence
    if result.verified_by in ("user", "email"):
        return bool(result.reference or result.detail)
    return False
