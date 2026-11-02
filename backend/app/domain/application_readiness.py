from __future__ import annotations


def readiness_label(checks_pass: bool, owner_decision: str | None) -> str:
    """Keep mechanical eligibility distinct from an explicit owner decision."""
    if owner_decision == "READY_FOR_MANUAL_SUBMISSION":
        return "READY_FOR_MANUAL_SUBMISSION"
    if owner_decision == "NEEDS_REVIEW" or not checks_pass:
        return "NEEDS_REVIEW"
    return "ELIGIBLE_FOR_OWNER_REVIEW"
