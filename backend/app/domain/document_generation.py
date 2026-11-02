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
    title=_header(job_title); employer=_header(company)
    if kind==DocumentKind.COVER_LETTER:
        if not title or not employer:
            raise ValueError("cover letter requires the role and employer from the job record")
        role_terms=set(re.findall(r"[a-z0-9+#]+",title.casefold()))
        def relevance(identity:str)->tuple[int,int,str]:
            fact=rows[identity];text=evidence[identity].casefold();section=str(fact.get("section","")).casefold()
            score=sum(2 for term in role_terms if len(term)>2 and term in text)
            score+=sum(1 for term in ("support","troubleshoot","incident","customer","windows","linux","network","endpoint","documentation") if term in text)
            score+=2 if section in {"profile","work experience","technical skills"} else 0
            return (-score,int(fact.get("page") or 0),identity)
        profile_ids=[i for i in ordered if str(rows[i].get("section","")).casefold()=="profile"]
        selected=([profile_ids[0]] if profile_ids else [])
        selected.extend(i for i in sorted(ordered,key=relevance) if i not in selected)
        selected=selected[:3]
        if not selected: raise ValueError("no eligible evidence is available for a cover letter")
        profile_claim=evidence[profile_ids[0]] if profile_ids else evidence[selected[0]]
        body=[f"Re: {title} — {employer}","","Dear Hiring Team,", "",
          f"I am applying for the {title} opportunity at {employer}. My resume describes my background as: “{profile_claim}”",
          "", "Relevant experience from my resume includes:"]
        body.extend(f"• {evidence[identity]}" for identity in selected)
        body.extend(("",f"I would welcome the opportunity to discuss how this experience could support {employer}'s team. Thank you for your time and consideration.","","Sincerely,","Pratik Aeraple"))
        return GeneratedDocument(kind,"\n".join(body)+"\n",
          tuple({"fact_id":identity,"page":rows[identity].get("page"),"section":rows[identity].get("section"),
                 "source":rows[identity].get("source","canonical-resume")} for identity in selected))

    if kind==DocumentKind.ATS_RESUME:
        # Keep factual wording verbatim and organize it into a compact ATS-safe
        # text layout. A tailored variant is a filtered view of evidence, never
        # a source of newly invented claims.
        lines=["PRATIK AERAPLE",f"TARGET ROLE: {title}" if title else "TECHNICAL SUPPORT · SYSTEMS · INFRASTRUCTURE",""]
        profile=[i for i in ordered if str(rows[i].get("section","")).casefold()=="profile"]
        skills=[i for i in ordered if str(rows[i].get("section","")).casefold()=="technical skills" and
                any(word in evidence[i].casefold() for word in ("systems & infrastructure","cloud & platforms","networking & security","automation & operations","technical documentation"))]
        experience=[i for i in ordered if any(word in evidence[i].casefold() for word in
          ("server administrator","system administrator co-op","system administrator |","hospitality manager",
           "administered and monitored","resolved ","deployed and maintained azure-hosted","automated repeatable infrastructure",
           "supported monitoring, capacity planning"))]
        education=[i for i in ordered if str(rows[i].get("section","")).casefold()=="education"]
        projects=[i for i in ordered if any(name in evidence[i].casefold() for name in
          ("brutehost |","domaincore |","azurebridge |","netshield |","recoverops |","deployment:"))]
        sections=[("PROFESSIONAL SUMMARY",profile[:4]),("TECHNICAL SKILLS",skills[:8]),
                  ("RELEVANT EXPERIENCE",experience[:12]),("EDUCATION",education[:6]),("SELECTED PROJECTS",projects[:10])]
        used=set()
        for heading,identities in sections:
            identities=[i for i in identities if i not in used]
            if not identities: continue
            lines.extend((heading,*[f"• {evidence[i]}" for i in identities],""));used.update(identities)
        selected=sorted(used,key=lambda identity:(int(rows[identity].get("page") or 0),identity))
        if not selected: raise ValueError("no eligible source facts were available for the resume variant")
        claims=[Claim(evidence[identity],(identity,)) for identity in selected]
        validate_claims(claims,evidence)
        return GeneratedDocument(kind,"\n".join(lines).rstrip()+"\n",
          tuple({"fact_id":identity,"page":rows[identity].get("page"),"section":rows[identity].get("section"),
                 "source":rows[identity].get("source","canonical-resume")} for identity in selected))

    lines=[]
    if kind==DocumentKind.SCREENING_PACKAGE:
        lines.append("Verified source statements for owner review:")
    elif kind==DocumentKind.APPLICATION_SUMMARY:
        lines.append(f"Prepared application: {title or 'Role'} · {employer or 'Employer'}")
    for identity in ordered:
        fact=rows[identity]
        section=_header(str(fact.get("section","")),80)
        if kind==DocumentKind.APPLICATION_SUMMARY and (not lines or lines[-1]!=section):
            lines.extend(("",section))
        lines.append(f"- {evidence[identity]}")
    if not ordered:
        raise ValueError("no eligible source facts were available")
    return GeneratedDocument(kind,"\n".join(lines).strip()+"\n",
        tuple({"fact_id":identity,"page":rows[identity].get("page"),
               "section":rows[identity].get("section"),"source":rows[identity].get("source","canonical-resume")}
              for identity in ordered))
