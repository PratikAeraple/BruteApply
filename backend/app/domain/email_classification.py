"""Conservative classification of untrusted recruiter/application email."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re


class EmailCategory(StrEnum):
    APPLICATION_CONFIRMATION = "APPLICATION_CONFIRMATION"
    RECRUITER_RESPONSE = "RECRUITER_RESPONSE"
    INTERVIEW_INVITATION = "INTERVIEW_INVITATION"
    ASSESSMENT = "ASSESSMENT"
    REJECTION = "REJECTION"
    OFFER = "OFFER"
    OTHER = "OTHER"
    NEEDS_REVIEW = "NEEDS_REVIEW"


PATTERNS = {
    EmailCategory.APPLICATION_CONFIRMATION: re.compile(r"application (?:was )?(?:received|submitted)|thank you for applying|submission confirmation",re.I),
    EmailCategory.INTERVIEW_INVITATION: re.compile(r"(?:invite|invitation|schedule|availability).{0,50}interview|interview.{0,50}(?:invite|schedule|availability)",re.I),
    EmailCategory.ASSESSMENT: re.compile(r"assessment|technical test|coding test|skills test",re.I),
    EmailCategory.REJECTION: re.compile(r"(?:not|unsuccessful|unable to) (?:moving forward|selected)|position.{0,30}filled|application.{0,30}declined",re.I),
    EmailCategory.OFFER: re.compile(r"(?:job|employment|written) offer|offer of employment",re.I),
    EmailCategory.RECRUITER_RESPONSE: re.compile(r"recruiter|reaching out|quick call|phone screen|screening call",re.I),
}
INJECTION = re.compile(r"ignore (?:all )?(?:previous|prior|above) instructions|reveal (?:the )?(?:system prompt|credentials|token)|send .*?(?:resume|database|profile) to",re.I)


@dataclass(frozen=True)
class ClassifiedEmail:
    category: EmailCategory
    confidence: float
    requires_owner_review: bool
    contains_suspicious_instruction: bool
    matched_signals: tuple[str,...]


def classify_email(subject: str, body_text: str) -> ClassifiedEmail:
    """Classify bounded text as data. No actions, links, or embedded instructions execute."""
    if not isinstance(subject,str) or not isinstance(body_text,str):
        raise ValueError("email content must be text")
    if len(subject)>2000 or len(body_text)>200_000:
        raise ValueError("email content exceeds the configured limit")
    text=f"{subject}\n{body_text}"
    matched=tuple(category.value for category,pattern in PATTERNS.items() if pattern.search(text))
    suspicious=bool(INJECTION.search(text))
    if suspicious or len(matched)>1:
        category=EmailCategory.NEEDS_REVIEW
    elif matched:
        category=EmailCategory(matched[0])
    else:
        category=EmailCategory.OTHER
    confidence=.92 if len(matched)==1 and not suspicious else .25 if matched or suspicious else .5
    return ClassifiedEmail(category,confidence,suspicious or category in {EmailCategory.NEEDS_REVIEW,EmailCategory.OTHER},
                           suspicious,matched)
