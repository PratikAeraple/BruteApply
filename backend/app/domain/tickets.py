from __future__ import annotations


TICKET_TRANSITIONS = {
    "OPEN": {"IN_PROGRESS", "RESOLVED", "CANCELLED"},
    "IN_PROGRESS": {"OPEN", "RESOLVED", "CANCELLED"},
    "RESOLVED": {"OPEN"},
    "CANCELLED": {"OPEN"},
}


def transition_ticket(current: str, target: str, *, decision: str | None = None) -> str:
    if target not in TICKET_TRANSITIONS.get(current, set()):
        raise ValueError("invalid ticket transition")
    if target == "RESOLVED" and not decision:
        raise ValueError("resolution requires an explicit decision")
    return target
