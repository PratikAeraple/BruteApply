from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re

from .document_provenance import Claim, validate_claims, validate_generation_inputs


class DocumentKind(StrEnum):
    ATS_RESUME = "ATS_RESUME"
    COVER_LETTER = "COVER_LETTER"
    SCREENING_PACKAGE = "SCREENING_PACKAGE"
    APPLICATION_SUMMARY = "APPLICATION_SUMMARY"


@dataclass(frozen=True)
class GeneratedDocument:
    kind: DocumentKind
    text: str
    provenance: tuple[dict[str, object], ...]


def _header(value: str, limit: int = 180) -> str:
    value = re.sub(r"[\x00-\x1f\x7f]", " ", value)
    return " ".join(value.split())[:limit]


def generate_document(kind: DocumentKind, facts: list[dict], *, resume_active: bool,
                      job_title: str = "", company: str = "") -> GeneratedDocument:
    evidence = validate_generation_inputs(facts, resume_active=resume_active)
    rows={str(item["id"]):item for item in facts if str(item.get("id")) in evidence}
    ordered=sorted(rows, key=lambda identity:(str(rows[identity].get("section","")),int(rows[identity].get("page") or 0),identity))
    claims=[Claim(evidence[identity],(identity,)) for identity in ordered]
    validate_claims(claims,evidence)
    lines=[]
    title=_header(job_title); employer=_header(company)
    if kind==DocumentKind.COVER_LETTER:
        if not title or not employer:
            raise ValueError("cover letter requires the role and employer from the job record")
        lines.extend((f"Application for {title}",employer,"","Relevant resume statements:"))
    elif kind==DocumentKind.SCREENING_PACKAGE:
        lines.append("Verified source statements for owner review:")
    elif kind==DocumentKind.APPLICATION_SUMMARY:
        lines.append(f"Prepared application: {title or 'Role'} · {employer or 'Employer'}")
    for identity in ordered:
        fact=rows[identity]
        if kind==DocumentKind.ATS_RESUME:
            section=_header(str(fact.get("section","")),80)
            if not lines or lines[-1] != section:
                lines.extend(("",section))
        lines.append(f"- {evidence[identity]}")
    if not ordered:
        raise ValueError("no eligible source facts were available")
    return GeneratedDocument(kind,"\n".join(lines).strip()+"\n",
        tuple({"fact_id":identity,"page":rows[identity].get("page"),
               "section":rows[identity].get("section"),"source":rows[identity].get("source","canonical-resume")}
              for identity in ordered))
