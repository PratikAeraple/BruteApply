from __future__ import annotations

import hashlib
import json


APPLICATION_HEADERS = (
    "Application ID", "Date Found", "Date Applied", "Company", "Job Title", "Category", "Priority",
    "City", "Province", "Work Mode", "Employment Type", "Salary / Rate", "Source", "Job URL",
    "Resume Version", "Cover Letter", "Status", "Last Status Update", "Recruiter / Contact",
    "Contact Email", "Contact Phone", "Next Follow-up", "Follow-up Status", "Interview Date",
    "Interview Stage / Notes", "Assessment / Test", "Job Posting Deadline", "Days Since Applied",
    "Days to Follow-up", "Outcome / Rejection Reason", "Offer Details", "Notes",
)
FORMULA_COLUMNS = {0, 27, 28}

# Values are indexed by the existing header. Formula-derived Application ID and
# date calculations remain sheet-owned; no write payload may include them.
SYSTEM_OWNED = {1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 17}
OWNER_OWNED = {18, 19, 20, 22, 24, 25, 29, 30, 31}
MERGEABLE = {2, 16, 21, 23, 26}


def row_hash(cells: list[object]) -> str:
    clean = [None if i in FORMULA_COLUMNS else v for i, v in enumerate(cells)]
    return hashlib.sha256(json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def validate_sheet_row(row: list[object]) -> None:
    if len(row) != len(APPLICATION_HEADERS):
        raise ValueError("sheet row width does not match the established schema")


def database_owned_updates(row: list[object]) -> dict[int, object]:
    validate_sheet_row(row)
    # Sparse update payload: formula columns and owner-owned columns are absent,
    # so they cannot be accidentally cleared by a rectangular write.
    return {index: row[index] for index in SYSTEM_OWNED}


def merge_owner_edit(database: list[object], sheet: list[object]) -> tuple[list[object], list[int]]:
    validate_sheet_row(database); validate_sheet_row(sheet)
    merged = list(database); conflicts = []
    for index in OWNER_OWNED | MERGEABLE:
        if index in MERGEABLE and database[index] not in (None, "") and sheet[index] not in (None, "") and database[index] != sheet[index]:
            conflicts.append(index)
        elif sheet[index] not in (None, ""):
            merged[index] = sheet[index]
    return merged, conflicts
