from copy import deepcopy
from datetime import datetime, timezone

import pytest

from app.domain.sheet_sync import APPLICATION_HEADERS, FORMULA_COLUMNS
from app.integrations.sheet_sync_runner import (
    _db_row, _legacy_date_mismatch, _map_status, _metadata_row, _owner_fields, _verify_preservation,
)


class MetadataAPI:
    def search_developer_metadata(self, **kwargs):
        return [{"developerMetadata": {"metadataId": 42, "location": {
            "dimensionRange": {"sheetId": 7, "dimension": "ROWS", "startIndex": 9, "endIndex": 10}
        }}}]


def _export():
    cells=[{} for _ in APPLICATION_HEADERS]
    cells[0]={"userEnteredValue":{"formulaValue":"=ROW()-1"},"userEnteredFormat":{"numberFormat":{"type":"NUMBER"}}}
    cells[1]={"userEnteredValue":{"stringValue":"Date Found"},"dataValidation":{"condition":{"type":"TEXT_IS_VALID_EMAIL"}}}
    return {"spreadsheetId":"sheet-id","properties":{"timeZone":"America/Toronto"},"sheets":[
      {"properties":{"sheetId":7,"title":"Applications","gridProperties":{"rowCount":1000,"columnCount":32}},
       "data":[{"rowData":[{"values":cells}]}]},
      {"properties":{"sheetId":8,"title":"Dashboard"},"data":[]},
      {"properties":{"sheetId":9,"title":"Lists"},"data":[]},
    ]}


def test_metadata_identity_tracks_row_not_visible_application_id():
    assert _metadata_row(MetadataAPI(),"0fdd6c23-0647-49f7-a3fc-e02638c44518")== (10,42)


def test_legacy_date_is_preserved_only_for_first_exact_url_association():
    url="https://careers.example/job/123"
    assert _legacy_date_mismatch(False,url,url,{1})
    assert not _legacy_date_mismatch(True,url,url,{1})
    assert not _legacy_date_mismatch(False,"https://careers.example/job/other",url,{1})
    assert not _legacy_date_mismatch(False,url,url,{3})


def test_sheet_status_never_proves_submission():
    assert _map_status("Applied","QUALIFIED")==("SUBMISSION_UNVERIFIED",None)
    assert _map_status("To Apply","SUBMITTED")== (None,"stale_status")
    assert _map_status("Recruiter Screen","SUBMITTED")== (None,"status_regression")


def test_owner_fields_are_bounded_and_priority_is_validated():
    row=[None]*32;row[6]="High";row[31]="owner note"
    assert _owner_fields(row)=={"priority":"High","notes":"owner note"}
    row[6]="Urgent"
    with pytest.raises(RuntimeError,match="priority"):
        _owner_fields(row)
    row=[None]*32;row[31]="x"*4001
    with pytest.raises(RuntimeError,match="size"):
        _owner_fields(row)


def test_database_row_leaves_formulas_and_owner_fields_unwritten():
    record={"company":"Example","title":"Support Analyst","state":"QUALIFIED",
      "canonical_url":"https://jobs.example/1","discovered_at":datetime(2026,11,2,tzinfo=timezone.utc),
      "updated_at":datetime(2026,11,2,tzinfo=timezone.utc)}
    row=_db_row(record)
    assert len(row)==32
    assert row[3:5]==["Example","Support Analyst"]
    assert row[13]=="https://jobs.example/1"
    assert row[16]=="To Apply"
    assert all(row[index] is None for index in FORMULA_COLUMNS)
    assert row[6] is None and row[31] is None


def test_preservation_allows_only_declared_values_and_never_formula_edits():
    before=_export();after=deepcopy(before)
    _verify_preservation(before,after,set())
    after["sheets"][0]["data"][0]["rowData"][0]["values"][1]["userEnteredValue"]={"stringValue":"Other"}
    with pytest.raises(RuntimeError,match="unplanned cell"):
        _verify_preservation(before,after,set())
    after=deepcopy(before)
    after["sheets"][0]["data"][0]["rowData"][0]["values"][0]["userEnteredValue"]={"formulaValue":"=1"}
    with pytest.raises(RuntimeError,match="formula"):
        _verify_preservation(before,after,{(0,0)})


def test_preservation_detects_validation_or_format_changes():
    before=_export();after=deepcopy(before)
    after["sheets"][0]["data"][0]["rowData"][0]["values"][1]["dataValidation"]={"condition":{"type":"ONE_OF_LIST"}}
    with pytest.raises(RuntimeError,match="format or validation"):
        _verify_preservation(before,after,set())
