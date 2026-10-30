from __future__ import annotations

from dataclasses import dataclass


ALLOWED_FACT_STATUSES = {"RESUME_VERIFIED", "OWNER_APPROVED"}


@dataclass(frozen=True)
class Claim:
    text: str
    fact_ids: tuple[str, ...]


def validate_claims(claims: list[Claim], source_facts: dict[str, str]) -> None:
    if not claims:
        raise ValueError("generated document must contain attributable claims")
    for claim in claims:
        if not claim.text.strip() or not claim.fact_ids:
            raise ValueError("every generated claim requires source facts")
        if any(identity not in source_facts for identity in claim.fact_ids):
            raise ValueError("generated claim cites an unverified or unavailable fact")
        evidence = " ".join(source_facts[identity] for identity in claim.fact_ids)
        if " ".join(claim.text.casefold().split()) not in " ".join(evidence.casefold().split()):
            raise ValueError("generated claim is not supported verbatim by its cited facts")


def validate_generation_inputs(facts: list[dict], *, resume_active: bool) -> dict[str, str]:
    if not resume_active:
        raise ValueError("generation requires an active canonical resume")
    allowed = {str(fact["id"]):str(fact["text"]) for fact in facts
               if fact.get("status") in ALLOWED_FACT_STATUSES and isinstance(fact.get("text"),str)}
    if not allowed:
        raise ValueError("generation requires verified or owner-approved facts")
    return allowed
