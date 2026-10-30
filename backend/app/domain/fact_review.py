from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re


class FactStatus(StrEnum):
    NEEDS_REVIEW = "NEEDS_REVIEW"
    RESUME_VERIFIED = "RESUME_VERIFIED"
    OWNER_APPROVED = "OWNER_APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


SENSITIVE = re.compile(r"work_author|immigra|disab|medical|race|ethnic|relig|gender|sexual|veteran|criminal|security_clearance|salary", re.I)
SECTIONS = {"PERSONAL", "CONTACT", "PROFILE", "TECHNICAL SKILLS", "WORK EXPERIENCE", "EDUCATION", "CERTIFICATIONS", "PROJECTS", "OTHER"}


@dataclass(frozen=True)
class ResumeFact:
    text: str
    page: int
    section: str
    confidence: float
    sensitivity: str = "NORMAL"


def validate_resume_fact(fact: ResumeFact, page_count: int) -> ResumeFact:
    text = " ".join(fact.text.split())
    section = " ".join(fact.section.upper().split())
    if not text or len(text) > 5000 or not 1 <= fact.page <= page_count:
        raise ValueError("resume fact has invalid text or page provenance")
    if section not in SECTIONS or not 0.0 <= fact.confidence <= 1.0:
        raise ValueError("resume fact has invalid section or confidence")
    sensitivity = "SENSITIVE" if SENSITIVE.search(text) else fact.sensitivity
    if sensitivity not in {"NORMAL", "SENSITIVE", "LEGAL"}:
        raise ValueError("invalid sensitivity classification")
    return ResumeFact(text, fact.page, section, fact.confidence, sensitivity)


def initial_status(*, canonical_resume: bool, confidence: float, sensitivity: str) -> FactStatus:
    # Verbatim resume extraction is evidence of what the document says, not an
    # owner-approved legal/sensitive answer. Ambiguous or sensitive facts queue.
    if canonical_resume and confidence >= 0.95 and sensitivity == "NORMAL":
        return FactStatus.RESUME_VERIFIED
    return FactStatus.NEEDS_REVIEW


def can_bulk_transition(current: FactStatus, target: FactStatus, *, canonical: bool,
                        confidence: float, sensitivity: str) -> bool:
    if target == FactStatus.OWNER_APPROVED:
        return True
    if target == FactStatus.RESUME_VERIFIED:
        return canonical and confidence >= 0.95 and sensitivity == "NORMAL"
    return target in {FactStatus.NEEDS_REVIEW, FactStatus.REJECTED, FactStatus.SUPERSEDED}
