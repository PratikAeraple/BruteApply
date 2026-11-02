from copy import deepcopy
from datetime import datetime, timezone

import pytest

from app.domain.sheet_sync import APPLICATION_HEADERS, FORMULA_COLUMNS
from app.integrations.sheet_sync_runner import (
    _db_row, _find_preformatted_row, _legacy_date_mismatch, _safe_system_timestamp_refresh, _is_timestamp_only_conflict, _map_status, _metadata_row,
    _owner_fields, _restore_written_cell_formats, _verify_preservation,
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


def test_new_application_rows_are_reserved_once_per_sync():
    rows=[]
    for _ in range(3):
        row=[None]*32
        for index in FORMULA_COLUMNS:
            row[index]="=ROW()-1"
        rows.append(row)
    reserved=set()
    first=_find_preformatted_row(rows,reserved)
    assert first==2
    reserved.add(first)
    second=_find_preformatted_row(rows,reserved)
    assert second==3
    reserved.add(second)
    assert _find_preformatted_row(rows,reserved)==4


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


def test_date_found_uses_tracker_local_calendar_day():
    record={"company":"Example","title":"Support Analyst","state":"QUALIFIED",
      "canonical_url":"https://jobs.example/1",
      "discovered_at":datetime(2026,11,3,2,30,tzinfo=timezone.utc),
      "updated_at":datetime(2026,11,3,2,30,tzinfo=timezone.utc)}
    # UTC 02:30 is still the previous calendar day in Toronto.
    assert _db_row(record)[1]=="2026-11-02"


def test_only_bounded_forward_timezone_aware_system_timestamp_is_auto_refreshable():
    assert _safe_system_timestamp_refresh("2026-11-02T21:21:42.373253+00:00",
                                          "2026-11-02T21:21:55.554776+00:00")
    assert not _safe_system_timestamp_refresh("2026-11-02T21:21:42+00:00",
                                               "2026-11-02T16:21:42-05:00")
    assert not _safe_system_timestamp_refresh("2026-11-02T21:21:42",
                                               "2026-11-02T21:21:55+00:00")
    assert not _safe_system_timestamp_refresh("owner edit","2026-11-02T21:21:55+00:00")
    assert not _safe_system_timestamp_refresh("2026-11-01T21:21:42+00:00",
                                               "2026-11-02T21:21:55+00:00")
    assert _is_timestamp_only_conflict(["Last Status Update"],"2026-11-02T21:21:42+00:00",
                                       "2026-11-02T21:21:55+00:00")
    assert not _is_timestamp_only_conflict(["Status","Last Status Update"],"2026-11-02T21:21:42+00:00",
                                           "2026-11-02T21:21:55+00:00")


def test_multiple_new_rows_and_metadata_identity_remain_distinct():
    class DynamicMetadataAPI:
        def __init__(self):self.metadata=[]
        def search_developer_metadata(self,**kwargs):
            return [{"developerMetadata":{"metadataId":i+1,"metadataKey":key,"metadataValue":value,
              "location":{"dimensionRange":{"sheetId":7,"dimension":"ROWS","startIndex":row-1,"endIndex":row}}}}
              for i,(key,value,row) in enumerate(self.metadata) if key==kwargs["key"] and value==kwargs["value"]]
    rows=[]
    for _ in range(4):
        row=[None]*32
        for index in FORMULA_COLUMNS:row[index]="=ROW()-1"
        rows.append(row)
    reserved=set();api=DynamicMetadataAPI();assigned=[]
    for i in range(4):
        app_id=f"application-{i}"
        row=_find_preformatted_row(rows,reserved);assert row is not None
        reserved.add(row);assigned.append(row)
        api.metadata.append(("bruteapply_application_uuid",app_id,row))
    assert assigned==[2,3,4,5]
    assert len(set(assigned))==4


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


def test_written_cell_format_restoration_is_scoped_to_sync_cells():
    before=_export();after=deepcopy(before)
    after["sheets"][0]["data"][0]["rowData"][0]["values"][3]["userEnteredFormat"]={"textFormat":{"bold":True}}
    class FormatAPI:
        def __init__(self): self.requests=[]
        def batch_update_requests(self, **kwargs): self.requests.extend(kwargs["requests"])
    api=FormatAPI()
    assert _restore_written_cell_formats(api,before,after,{(0,3)})==1
    assert api.requests[0]["repeatCell"]["range"]["startColumnIndex"]==3
    with pytest.raises(RuntimeError,match="outside synchronization"):
        _restore_written_cell_formats(api,before,after,set())
