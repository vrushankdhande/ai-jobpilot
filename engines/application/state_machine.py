"""Application lifecycle. Every state change goes through ``check_transition``; anything not listed is rejected.

SUBMITTED / VERIFIED are only reachable with evidence (see submission_verifier): clicking the submit button never sets them.
"""
from enum import Enum


class AppState(str, Enum):
    DISCOVERED = "DISCOVERED"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    AWAITING_USER_INPUT = "AWAITING_USER_INPUT"
    APPROVED = "APPROVED"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    SUBMISSION_FAILED = "SUBMISSION_FAILED"
    VERIFICATION_PENDING = "VERIFICATION_PENDING"
    VERIFIED = "VERIFIED"
    MANUAL_HANDOFF_REQUIRED = "MANUAL_HANDOFF_REQUIRED"


S = AppState
TRANSITIONS: dict[AppState, frozenset[AppState]] = {
    S.DISCOVERED: frozenset({S.READY_FOR_REVIEW, S.AWAITING_USER_INPUT, S.MANUAL_HANDOFF_REQUIRED}),
    S.AWAITING_USER_INPUT: frozenset({S.AWAITING_USER_INPUT, S.READY_FOR_REVIEW, S.MANUAL_HANDOFF_REQUIRED}),
    S.READY_FOR_REVIEW: frozenset({S.READY_FOR_REVIEW, S.AWAITING_USER_INPUT, S.APPROVED, S.MANUAL_HANDOFF_REQUIRED}),
    # approval is withdrawn (back to review) when anything it covered changes; the submit attempt is the only way on
    S.APPROVED: frozenset({S.SUBMITTING, S.READY_FOR_REVIEW, S.AWAITING_USER_INPUT, S.MANUAL_HANDOFF_REQUIRED}),
    S.SUBMITTING: frozenset({S.SUBMITTED, S.SUBMISSION_FAILED, S.VERIFICATION_PENDING}),   # VERIFIED only after SUBMITTED (two recorded steps)
    # back to MANUAL_HANDOFF_REQUIRED only to undo the candidate's OWN 'I applied' record (no JobPilot attempt, not verified); the
    # service enforces that - an application JobPilot submitted can never be un-submitted
    S.SUBMITTED: frozenset({S.VERIFIED, S.MANUAL_HANDOFF_REQUIRED}),
    # ambiguous outcome: only a human (or later reliable evidence) resolves it - never an automatic resubmission
    S.VERIFICATION_PENDING: frozenset({S.SUBMITTED, S.VERIFIED, S.SUBMISSION_FAILED}),
    # a failed attempt may be prepared again (only when the failure was certain, see retry_manager) or handed to the user
    S.SUBMISSION_FAILED: frozenset({S.READY_FOR_REVIEW, S.AWAITING_USER_INPUT, S.MANUAL_HANDOFF_REQUIRED}),
    # the candidate applied themselves: SUBMITTED (their word) first; VERIFIED is a second, separate step with a reference/confirmation
    S.MANUAL_HANDOFF_REQUIRED: frozenset({S.SUBMITTED, S.READY_FOR_REVIEW, S.AWAITING_USER_INPUT}),
    S.VERIFIED: frozenset(),  # terminal
}
TERMINAL = frozenset({S.VERIFIED})
# states in which a submission may already exist at the employer (used for duplicate prevention)
SUBMISSION_MAY_EXIST = frozenset({S.SUBMITTING, S.SUBMITTED, S.VERIFICATION_PENDING, S.VERIFIED})
# states that hold the application open (block a second application for the same job)
ACTIVE = frozenset(set(AppState) - {S.SUBMISSION_FAILED})


class InvalidTransition(Exception):
    def __init__(self, src, dst):
        self.src, self.dst = src, dst
        super().__init__(f"Invalid application state change: {getattr(src, 'value', src)} -> {getattr(dst, 'value', dst)}")


def check_transition(src: AppState | str, dst: AppState | str) -> AppState:
    try:
        s, d = AppState(src), AppState(dst)
    except ValueError:
        raise InvalidTransition(src, dst) from None
    if d not in TRANSITIONS[s]:
        raise InvalidTransition(s, d)
    return d


def allowed_from(src: AppState | str) -> list[str]:
    return sorted(x.value for x in TRANSITIONS[AppState(src)])
