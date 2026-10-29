from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ApplicationState(StrEnum):
    DISCOVERED = "DISCOVERED"
    QUALIFIED = "QUALIFIED"
    SHORTLISTED = "SHORTLISTED"
    PREPARED = "PREPARED"
    ATTEMPTED = "ATTEMPTED"
    SUBMITTED = "SUBMITTED"
    SUBMISSION_UNVERIFIED = "SUBMISSION_UNVERIFIED"
    MANUAL_SUBMISSION_REQUIRED = "MANUAL_SUBMISSION_REQUIRED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"


TRANSITIONS = {
    ApplicationState.DISCOVERED: {ApplicationState.QUALIFIED, ApplicationState.REJECTED},
    ApplicationState.QUALIFIED: {ApplicationState.SHORTLISTED, ApplicationState.REJECTED},
    ApplicationState.SHORTLISTED: {ApplicationState.PREPARED, ApplicationState.REJECTED},
    ApplicationState.PREPARED: {ApplicationState.ATTEMPTED, ApplicationState.MANUAL_SUBMISSION_REQUIRED,
                                 ApplicationState.REJECTED},
    ApplicationState.ATTEMPTED: {ApplicationState.SUBMITTED, ApplicationState.SUBMISSION_UNVERIFIED,
                                  ApplicationState.MANUAL_SUBMISSION_REQUIRED},
    ApplicationState.SUBMISSION_UNVERIFIED: {ApplicationState.SUBMITTED,
                                               ApplicationState.MANUAL_SUBMISSION_REQUIRED},
}


def transition(current: ApplicationState, target: ApplicationState) -> ApplicationState:
    if target not in TRANSITIONS.get(current, set()):
        raise ValueError("invalid application state transition")
    return target


@dataclass(frozen=True)
class SubmissionContext:
    score: int
    minimum_score: int
    method_permitted: bool
    platform_allows_automation: bool
    required_information_complete: bool
    sensitive_question_unanswered: bool
    duplicate_check_passed: bool
    documents_valid: bool
    captcha_present: bool
    mfa_required: bool
    authentication_required: bool
    autonomous_enabled: bool
    emergency_stopped: bool


def submission_decision(ctx: SubmissionContext) -> tuple[bool, str]:
    checks = (
        (ctx.emergency_stopped, "EMERGENCY_STOP"),
        (not ctx.autonomous_enabled, "AUTONOMOUS_SUBMISSION_DISABLED"),
        (ctx.score < ctx.minimum_score, "BELOW_SCORE_THRESHOLD"),
        (not ctx.method_permitted or not ctx.platform_allows_automation, "MANUAL_SUBMISSION_REQUIRED"),
        (not ctx.required_information_complete, "MISSING_INFORMATION"),
        (ctx.sensitive_question_unanswered, "SENSITIVE_QUESTION"),
        (not ctx.duplicate_check_passed, "DUPLICATE_OR_UNCERTAIN"),
        (not ctx.documents_valid, "DOCUMENT_EXCEPTION"),
        (ctx.captcha_present, "CAPTCHA_REQUIRED"),
        (ctx.mfa_required, "MFA_REQUIRED"),
        (ctx.authentication_required, "AUTHENTICATION_REQUIRED"),
    )
    for blocked, reason in checks:
        if blocked:
            return False, reason
    return True, "POLICY_PASSED"


def submission_status(evidence: dict | None) -> ApplicationState:
    if evidence and evidence.get("reliable_confirmation") is True and evidence.get("confirmation_id"):
        return ApplicationState.SUBMITTED
    return ApplicationState.SUBMISSION_UNVERIFIED
