from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from ..domain.sheet_sync import APPLICATION_HEADERS, FORMULA_COLUMNS, MERGEABLE, SYSTEM_OWNED, validate_sheet_row


class SheetsValuesAPI(Protocol):
    def get(self, *, spreadsheet_id: str, range: str, value_render_option: str) -> list[list[Any]]: ...
    def batch_update(self, *, spreadsheet_id: str, data: list[dict[str, Any]], value_input_option: str) -> None: ...


class DriveSnapshotAPI(Protocol):
    def copy(self, *, file_id: str, name: str) -> dict[str, str]: ...


@dataclass(frozen=True)
class SnapshotProof:
    source_spreadsheet_id: str
    snapshot_file_id: str
    snapshot_name: str
    captured_at: datetime
    verified: bool


@dataclass(frozen=True)
class PlannedCellUpdate:
    row_number: int
    column_index: int
    value: Any


def create_snapshot(drive: DriveSnapshotAPI, spreadsheet_id: str, *, timestamp: datetime | None = None) -> SnapshotProof:
    if not spreadsheet_id or len(spreadsheet_id) > 200:
        raise ValueError("spreadsheet identity is invalid")
    captured = timestamp or datetime.now(timezone.utc)
    name = f"BruteApply pre-sync snapshot {captured.strftime('%Y%m%dT%H%M%SZ')}"
    copy = drive.copy(file_id=spreadsheet_id, name=name)
    snapshot_id = copy.get("id")
    mime_type = copy.get("mimeType")
    if not snapshot_id or mime_type != "application/vnd.google-apps.spreadsheet":
        raise RuntimeError("Google Drive did not confirm a native spreadsheet snapshot")
    return SnapshotProof(spreadsheet_id, snapshot_id, name, captured, True)


def read_applications(api: SheetsValuesAPI, spreadsheet_id: str) -> list[list[Any]]:
    rows = api.get(spreadsheet_id=spreadsheet_id, range="'Applications'!A1:AF1000",
                   value_render_option="FORMULA")
    if not rows or tuple(rows[0]) != APPLICATION_HEADERS:
        raise RuntimeError("existing Applications header does not match the supported 32-column schema")
    normalized=[]
    for row in rows[1:]:
        cells=list(row[:len(APPLICATION_HEADERS)])
        cells.extend([None] * (len(APPLICATION_HEADERS)-len(cells)))
        validate_sheet_row(cells)
        normalized.append(cells)
    return normalized


def plan_updates(row_number: int, database_row: list[Any], sheet_row: list[Any], *,
                 database_state: str, last_synced_hash: str | None) -> tuple[list[PlannedCellUpdate], list[int]]:
    """Plan sparse writes only; protect row formulas and detect stale owner edits."""
    validate_sheet_row(database_row); validate_sheet_row(sheet_row)
    if row_number < 2:
        raise ValueError("header row cannot be updated")
    from ..domain.sheet_sync import merge_owner_edit, row_hash
    merged, conflicts = merge_owner_edit(database_row, sheet_row)
    # A sheet status can never silently demote a verified database state.
    if database_state in {"SUBMITTED", "ASSESSMENT", "RECRUITER_SCREEN", "INTERVIEW_1", "INTERVIEW_2", "FINAL_INTERVIEW", "OFFER"}:
        if sheet_row[16] not in {None, "", database_row[16]}:
            conflicts.append(16)
            merged[16] = database_row[16]
    if last_synced_hash and row_hash(sheet_row) != last_synced_hash and conflicts:
        return [], sorted(set(conflicts))
    updates=[PlannedCellUpdate(row_number,index,merged[index]) for index in sorted(SYSTEM_OWNED|MERGEABLE)
             if index not in conflicts
             if index not in FORMULA_COLUMNS and merged[index] != sheet_row[index]]
    return updates, sorted(set(conflicts))


class GoogleSheetsSync:
    """Small transport adapter; callers must snapshot and dry-run before applying writes."""

    def __init__(self, sheets: SheetsValuesAPI, spreadsheet_id: str):
        if not spreadsheet_id:
            raise ValueError("spreadsheet ID is required")
        self.sheets=sheets
        self.spreadsheet_id=spreadsheet_id

    def read(self) -> list[list[Any]]:
        return read_applications(self.sheets,self.spreadsheet_id)

    def apply(self, updates: list[PlannedCellUpdate], *, snapshot: SnapshotProof | None = None,
              dry_run: bool = True) -> int:
        if not dry_run and (snapshot is None or snapshot.source_spreadsheet_id != self.spreadsheet_id
                            or not snapshot.verified or not snapshot.snapshot_file_id):
            raise RuntimeError("a verified pre-write snapshot of this spreadsheet is required")
        for update in updates:
            if update.column_index in FORMULA_COLUMNS or update.column_index not in SYSTEM_OWNED:
                raise ValueError("write plan includes a protected or owner-owned column")
            if update.row_number < 2 or update.row_number > 1000:
                raise ValueError("write plan row is outside the existing tracker bounds")
        if dry_run or not updates:
            return len(updates)
        # One atomic sparse batch ensures formulas, validation and formatting remain untouched.
        data=[]
        for update in updates:
            col=_column_letters(update.column_index + 1)
            data.append({"range":f"'Applications'!{col}{update.row_number}","values":[[update.value]]})
        self.sheets.batch_update(spreadsheet_id=self.spreadsheet_id,data=data,value_input_option="USER_ENTERED")
        return len(updates)


def _column_letters(number: int) -> str:
    if number < 1:
        raise ValueError("column number must be positive")
    result=""
    while number:
        number,remainder=divmod(number-1,26)
        result=chr(65+remainder)+result
    return result


def database_application_row(record: dict[str, Any]) -> list[Any]:
    """Map only system-owned application fields to the preserved 32-column layout."""
    row: list[Any] = [None] * len(APPLICATION_HEADERS)
    state=str(record.get("state", ""))
    status={
        "SUBMITTED":"Applied", "ASSESSMENT":"Assessment", "RECRUITER_SCREEN":"Recruiter Screen",
        "INTERVIEW_1":"Interview 1", "INTERVIEW_2":"Interview 2", "FINAL_INTERVIEW":"Final Interview",
        "OFFER":"Offer", "REJECTED":"Rejected", "WITHDRAWN":"Withdrawn", "ON_HOLD":"On Hold",
        "SUBMISSION_UNVERIFIED":"On Hold", "MANUAL_SUBMISSION_REQUIRED":"On Hold",
    }.get(state,"To Apply")
    values={1:record.get("date_found"),2:record.get("date_applied"),3:record.get("company"),
      4:record.get("title"),5:record.get("category"),7:record.get("city"),8:record.get("province"),
      9:record.get("work_mode"),10:record.get("employment_type"),11:record.get("salary_rate"),
      12:record.get("source"),13:record.get("job_url"),14:record.get("resume_version"),
      15:record.get("cover_letter_state"),16:status,17:record.get("updated_at")}
    for index,value in values.items():
        if index in SYSTEM_OWNED:
            row[index]=value.isoformat() if hasattr(value,"isoformat") else value
    return row
