from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import re

from pypdf import PdfReader


MAX_PAGES = 20
SECTION_MAP = {
    "profile": "PROFILE",
    "technical skills": "TECHNICAL SKILLS",
    "skills": "TECHNICAL SKILLS",
    "work experience": "WORK EXPERIENCE",
    "experience": "WORK EXPERIENCE",
    "education": "EDUCATION",
    "certifications": "CERTIFICATIONS",
    "projects": "PROJECTS",
    "contact": "CONTACT",
    "personal": "PERSONAL",
}
SENSITIVE_TEXT = re.compile(r"work author|immigra|disabilit|medical|race|ethnic|relig|gender identity|sexual orientation|veteran|criminal|security clearance|salary expectation", re.I)
CONTACT_TEXT = re.compile(r"@|(?:\+?1[ .()-]*)?(?:\(?\d{3}\)?[ .-]*)\d{3}[ .-]*\d{4}")


@dataclass(frozen=True)
class ExtractedFact:
    ordinal: int
    text: str
    page: int
    section: str
    confidence: float
    sensitivity: str


def extract_resume(data: bytes) -> tuple[int, list[ExtractedFact]]:
    """Extract source lines only. No scripts/actions are executed; callers must encrypt results."""
    reader = PdfReader(BytesIO(data), strict=True)
    if reader.is_encrypted:
        raise ValueError("encrypted PDFs must be unlocked before import")
    page_count = len(reader.pages)
    if not 1 <= page_count <= MAX_PAGES:
        raise ValueError("resume page count is outside the supported range")
    facts: list[ExtractedFact] = []
    section = "OTHER"
    ordinal = 0
    for page_number, page in enumerate(reader.pages, start=1):
        raw_text = page.extract_text(extraction_mode="plain") or ""
        for raw_line in raw_text.splitlines():
            line = " ".join(raw_line.split())
            if not line:
                continue
            normalized = line.casefold().strip(" :")
            if normalized in SECTION_MAP:
                section = SECTION_MAP[normalized]
                continue
            # Headers repeated on each page still retain exact page provenance.
            sensitivity = "LEGAL" if SENSITIVE_TEXT.search(line) else (
                "SENSITIVE" if section in {"CONTACT", "PERSONAL"} or CONTACT_TEXT.search(line) else "NORMAL")
            facts.append(ExtractedFact(ordinal, line[:5000], page_number, section, 0.99, sensitivity))
            ordinal += 1
            if ordinal > 500:
                raise ValueError("resume contains too many extracted text segments")
    return page_count, facts
