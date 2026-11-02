from __future__ import annotations

from collections.abc import Mapping, Sequence


def readiness_label(checks_pass: bool, owner_decision: str | None) -> str:
    """Keep mechanical eligibility distinct from an explicit owner decision."""
    if owner_decision == "READY_FOR_MANUAL_SUBMISSION":
        return "READY_FOR_MANUAL_SUBMISSION"
    if owner_decision == "NEEDS_REVIEW" or not checks_pass:
        return "NEEDS_REVIEW"
    return "ELIGIBLE_FOR_OWNER_REVIEW"


PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def latest_complete_document_packages(documents: Sequence[Mapping]) -> list[Mapping]:
    """Return newest PDF/DOCX pairs from the same generated source artifact.

    Current provenance uses ``source_document_id`` (the generator version
    predates ``generation_hash``). Grouping only by type would mix stale and
    current files; requiring a matched pair prevents that.
    """
    groups: dict[tuple, list[Mapping]] = {}
    for document in documents:
        provenance = document.get("provenance") or {}
        source_id = provenance.get("source_document_id") or provenance.get("generation_hash")
        if not source_id:
            continue
        key = (
            document["document_type"],
            source_id,
            provenance.get("variant"),
            provenance.get("resume_base_id"),
        )
        groups.setdefault(key, []).append(document)

    latest: dict[str, tuple] = {}
    for (kind, _, _, _), items in groups.items():
        formats = {item["mime_type"] for item in items}
        if PDF_MIME not in formats or DOCX_MIME not in formats:
            continue
        stamp = max(item["created_at"] for item in items)
        if kind not in latest or stamp > latest[kind][0]:
            latest[kind] = (stamp, items)

    selected = [item for _, items in latest.values() for item in items]
    return sorted(
        selected,
        key=lambda item: (
            item["document_type"],
            {PDF_MIME: 0, DOCX_MIME: 1, "text/plain": 2}.get(item["mime_type"], 3),
        ),
    )
