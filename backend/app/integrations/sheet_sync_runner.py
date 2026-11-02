"""Owner-private, idempotent PostgreSQL ↔ Google Sheets synchronization."""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import time
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from .. import config, db
from ..domain.sheet_sync import (APPLICATION_HEADERS, FORMULA_COLUMNS, OWNER_OWNED,
    SYSTEM_OWNED, MERGEABLE, row_hash)
from ..storage import decrypt_document, encrypt_document, load_key_file
from .google_oauth_rest import GoogleOAuthREST

SPREADSHEET_ID=config.GOOGLE_SHEETS_SPREADSHEET_ID
TAB="Applications"
METADATA_KEY="bruteapply_application_uuid"
OWNER_KEYS={2:"date_applied",6:"priority",18:"recruiter_contact",19:"contact_email",20:"contact_phone",
  21:"next_follow_up",22:"follow_up_status",23:"interview_date",24:"interview_notes",
  25:"assessment",26:"posting_deadline",29:"outcome",30:"offer_details",31:"notes"}
ALLOWED_PRIORITY={"High","Medium","Low"}
ALLOWED_STATUS={"To Apply","Applied","Assessment","Recruiter Screen","Interview 1",
  "Interview 2","Final Interview","Offer","Rejected","Withdrawn","On Hold"}


def _canonical(value: Any) -> bytes:
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),default=str).encode()


def _projection(export: dict[str,Any]) -> dict[str,Any]:
    result=[]
    for sheet in export.get("sheets",[]):
        grids=[]
        for grid in sheet.get("data",[]):
            rows=[]
            for row in grid.get("rowData",[]):
                rows.append([{k:cell[k] for k in ("userEnteredValue","userEnteredFormat","dataValidation","note") if k in cell}
                             for cell in row.get("values",[])])
            grids.append(rows)
        result.append({"properties":sheet.get("properties",{}),"data":grids})
    return {"spreadsheetId":export.get("spreadsheetId"),"properties":export.get("properties",{}),"sheets":result}


def _private_snapshot(client: GoogleOAuthREST) -> dict[str,Any]:
    state_dir=Path(os.environ["GOOGLE_SHEETS_STATE_DIR"])
    if state_dir.is_symlink() or not state_dir.is_dir() or stat.S_IMODE(state_dir.stat().st_mode)&0o007:
        raise RuntimeError("private Sheets state directory permissions are invalid")
    receipt=state_dir/"google-sheet-prewrite-snapshot.json"
    export=client.spreadsheet_export(spreadsheet_id=SPREADSHEET_ID)
    if export.get("spreadsheetId")!=SPREADSHEET_ID:
        raise RuntimeError("snapshot source identity mismatch")
    titles={s.get("properties",{}).get("title") for s in export.get("sheets",[])}
    if not {"Applications","Dashboard","Lists"}.issubset(titles):
        raise RuntimeError("required tracker tab missing")
    app_sheet=next(s for s in export["sheets"] if s["properties"]["title"]==TAB)
    grid=(app_sheet.get("data") or [{}])[0]
    header=[]
    for cell in (grid.get("rowData") or [{}])[0].get("values",[]):
        header.append(cell.get("userEnteredValue",{}).get("stringValue"))
    if tuple(header)!=APPLICATION_HEADERS:
        raise RuntimeError("Applications schema does not match the 32-column contract")
    export_bytes=_canonical(export)
    digest=hashlib.sha256(export_bytes).hexdigest()
    key=load_key_file(os.environ["DOCUMENT_ENCRYPTION_KEY_FILE"])
    stale=True
    if receipt.is_file() and not receipt.is_symlink():
        try:
            receipt_stat=receipt.stat()
            if stat.S_IMODE(receipt_stat.st_mode)&0o027:
                raise ValueError
            record=json.loads(receipt.read_text())
            if record.get("source_spreadsheet_id")==SPREADSHEET_ID and record.get("verified_private_encrypted_export"):
                identity=UUID(record["snapshot_id"])
                rel=record["storage_key"]
                if not rel.startswith("google-sheets/") or ".." in Path(rel).parts:
                    raise ValueError
                blob=(config.DOCUMENT_ROOT/rel).read_bytes()
                prior=decrypt_document(blob,key,identity)
                prior_export=json.loads(prior)
                stale=hashlib.sha256(prior).hexdigest()!=record.get("sha256") or _projection(prior_export)!=_projection(export)
                if not stale:
                    return {"receipt":record,"export":export,"refreshed":False}
        except Exception:
            stale=True
    if stale:
        identity=uuid4()
        storage=config.DOCUMENT_ROOT/"google-sheets"
        storage.mkdir(mode=0o700,parents=True,exist_ok=True);os.chmod(storage,0o700)
        name=f"{identity.hex}.json.enc"; dest=storage/name
        encrypted=encrypt_document(export_bytes,key,identity)
        fd=os.open(dest,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,"wb") as out: out.write(encrypted);out.flush();os.fsync(out.fileno())
        if decrypt_document(dest.read_bytes(),key,identity)!=export_bytes:
            raise RuntimeError("encrypted pre-write snapshot failed verification")
        record={"source_spreadsheet_id":SPREADSHEET_ID,"snapshot_id":str(identity),
          "storage_key":f"google-sheets/{name}","captured_at_utc":datetime.now(timezone.utc).isoformat(),
          "verified_private_encrypted_export":True,"sha256":hashlib.sha256(export_bytes).hexdigest(),
          "export_size_bytes":len(export_bytes),"tabs":sorted(titles),"applications_header_columns":32}
        temp=receipt.with_suffix(".tmp")
        fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o640)
        with os.fdopen(fd,"w") as out: json.dump(record,out,separators=(",",":"));out.write("\n");out.flush();os.fsync(out.fileno())
        os.replace(temp,receipt)
        return {"receipt":record,"export":export,"refreshed":True}
    raise RuntimeError("snapshot state unavailable")


def _worksheet(client:GoogleOAuthREST) -> tuple[int,list[list[Any]]]:
    meta=client.spreadsheet_metadata(spreadsheet_id=SPREADSHEET_ID)
    sheet=next((s for s in meta.get("sheets",[]) if s.get("properties",{}).get("title")==TAB),None)
    if not sheet: raise RuntimeError("Applications tab missing")
    sheet_id=sheet["properties"]["sheetId"]
    from .google_sheets import read_applications
    return sheet_id,read_applications(client,SPREADSHEET_ID)


def _records(conn) -> list[dict[str,Any]]:
    profile=conn.execute("SELECT id FROM candidate_profile ORDER BY created_at LIMIT 1").fetchone()
    if not profile: raise RuntimeError("candidate profile missing")
    # Qualified jobs become trackable once; this is not an application submission.
    candidates=conn.execute("""SELECT j.id FROM job j LEFT JOIN application a ON a.job_id=j.id AND a.profile_id=%s
      WHERE j.lifecycle IN ('QUALIFIED','SHORTLISTED','PREPARED') AND a.id IS NULL""",(profile["id"],)).fetchall()
    for candidate in candidates:
        app_id=uuid4(); job_id=candidate["id"]
        row=conn.execute("""INSERT INTO application(id,profile_id,job_id,idempotency_key,state)
          VALUES (%s,%s,%s,%s,'QUALIFIED') ON CONFLICT(profile_id,job_id) DO NOTHING RETURNING id""",
          (app_id,profile["id"],job_id,f"qualified-job:{job_id}")).fetchone()
        if row:
            conn.execute("INSERT INTO application_event(id,application_id,from_state,to_state,actor,evidence) VALUES (%s,%s,NULL,'QUALIFIED','sheet-sync',%s)",
                         (uuid4(),app_id,Jsonb({"source":"qualified job pipeline","submitted":False})))
    return conn.execute("""SELECT a.id AS application_id,a.profile_id,a.state,a.sheet_row,a.sheet_visible_id,
      a.date_applied,a.resume_id,a.cover_letter_document_id,a.updated_at,
      j.id AS job_id,j.company,j.title,j.city,j.province,j.work_mode,j.employment_type,
      j.salary_min,j.salary_max,j.currency,j.canonical_url,j.discovered_at,j.deadline,
      j.requirements,s.name AS source,r.label AS resume_version
      FROM application a JOIN job j ON j.id=a.job_id LEFT JOIN job_source s ON s.id=j.source_id
      LEFT JOIN resume r ON r.id=a.resume_id LEFT JOIN job_match jm ON jm.job_id=j.id AND jm.profile_id=a.profile_id
      WHERE a.profile_id=%s AND j.lifecycle IN ('QUALIFIED','SHORTLISTED','PREPARED')
      ORDER BY jm.score DESC NULLS LAST,jm.confidence DESC NULLS LAST,j.discovered_at,a.created_at""",(profile["id"],)).fetchall()


def _db_row(record:dict[str,Any]) -> list[Any]:
    row=[None]*32
    date_found=record["discovered_at"].date().isoformat() if record.get("discovered_at") else None
    salary=None
    if record.get("salary_min") is not None or record.get("salary_max") is not None:
        lo=record.get("salary_min");hi=record.get("salary_max")
        salary=f"{lo if lo is not None else ''}-{hi if hi is not None else ''} {record.get('currency') or 'CAD'}".strip()
    category=(record.get("requirements") or {}).get("category")
    states={"SUBMITTED":"Applied","ASSESSMENT":"Assessment","RECRUITER_SCREEN":"Recruiter Screen",
      "INTERVIEW_1":"Interview 1","INTERVIEW_2":"Interview 2","FINAL_INTERVIEW":"Final Interview",
      "OFFER":"Offer","REJECTED":"Rejected","WITHDRAWN":"Withdrawn","ON_HOLD":"On Hold",
      "SUBMISSION_UNVERIFIED":"On Hold","MANUAL_SUBMISSION_REQUIRED":"On Hold"}
    values={1:date_found,2:record.get("date_applied"),3:record["company"],4:record["title"],5:category,
      7:record.get("city"),8:record.get("province"),9:record.get("work_mode"),10:record.get("employment_type"),
      11:salary,12:record.get("source"),13:record["canonical_url"],14:record.get("resume_version"),
      15:"Prepared" if record.get("cover_letter_document_id") else None,
      16:states.get(record["state"],"To Apply"),17:record.get("updated_at"),26:record.get("deadline")}
    for index,value in values.items():
        if value is None: continue
        if hasattr(value,"isoformat"): value=value.isoformat()
        row[index]=value
    return row


def _metadata_row(client:GoogleOAuthREST, app_id:UUID) -> tuple[int|None,int|None]:
    matches=client.search_developer_metadata(spreadsheet_id=SPREADSHEET_ID,key=METADATA_KEY,value=str(app_id))
    if len(matches)>1: raise RuntimeError("duplicate stable application metadata")
    if not matches:return None,None
    md=matches[0].get("developerMetadata",{})
    dim=(md.get("location",{}).get("dimensionRange") or {})
    return (dim.get("startIndex")+1 if isinstance(dim.get("startIndex"),int) else None,md.get("metadataId"))


def _cell_value(value:Any) -> dict[str,Any]:
    if value is None:return {"stringValue":""}
    if isinstance(value,bool):return {"boolValue":value}
    if isinstance(value,(int,float)):return {"numberValue":value}
    return {"stringValue":str(value)}


def _owner_fields(row:list[Any]) -> dict[str,Any]:
    result={}
    for index,key in OWNER_KEYS.items():
        value=row[index] if index<len(row) else None
        if value in (None,""):continue
        if isinstance(value,str): value=value.strip()
        if key=="priority" and value not in ALLOWED_PRIORITY:
            raise RuntimeError("invalid owner priority value")
        if isinstance(value,str) and len(value)>4000: raise RuntimeError("owner field exceeds safe size")
        result[key]=value
    return result


def _map_status(value:Any,current:str) -> tuple[str|None,str|None]:
    if value in (None,""):return None,None
    text=str(value).strip()
    if text not in ALLOWED_STATUS: raise RuntimeError("unrecognized owner status value")
    current_sheet={"SUBMITTED":"Applied","ASSESSMENT":"Assessment","RECRUITER_SCREEN":"Recruiter Screen",
      "INTERVIEW_1":"Interview 1","INTERVIEW_2":"Interview 2","FINAL_INTERVIEW":"Final Interview","OFFER":"Offer",
      "REJECTED":"Rejected","WITHDRAWN":"Withdrawn","ON_HOLD":"On Hold","SUBMISSION_UNVERIFIED":"On Hold"}.get(current)
    if text==current_sheet:return None,None
    advanced={"SUBMITTED","SUBMISSION_UNVERIFIED","ASSESSMENT","RECRUITER_SCREEN","INTERVIEW_1","INTERVIEW_2","FINAL_INTERVIEW","OFFER","REJECTED","WITHDRAWN","ON_HOLD"}
    if current in advanced and text=="To Apply":return None,"stale_status"
    mapping={"To Apply":None,"On Hold":"ON_HOLD","Rejected":"REJECTED","Withdrawn":"WITHDRAWN",
      "Applied":"SUBMISSION_UNVERIFIED","Assessment":"SUBMISSION_UNVERIFIED","Recruiter Screen":"SUBMISSION_UNVERIFIED",
      "Interview 1":"SUBMISSION_UNVERIFIED","Interview 2":"SUBMISSION_UNVERIFIED","Final Interview":"SUBMISSION_UNVERIFIED","Offer":"SUBMISSION_UNVERIFIED"}
    target=mapping[text]
    if text=="To Apply" and current in advanced:return None,"stale_status"
    if target and current in advanced and current!=target:return None,"status_regression"
    return target,None


def _cell(export:dict[str,Any],title:str,row:int,col:int)->dict[str,Any]:
    sheet=next(s for s in export.get("sheets",[]) if s.get("properties",{}).get("title")==title)
    grids=sheet.get("data",[])
    if not grids:return {}
    rows=grids[0].get("rowData",[])
    if row>=len(rows):return {}
    cells=rows[row].get("values",[])
    return cells[col] if col<len(cells) else {}


def _verify_preservation(before:dict[str,Any],after:dict[str,Any],allowed:set[tuple[int,int]])->None:
    if before.get("spreadsheetId")!=after.get("spreadsheetId") or before.get("properties")!=after.get("properties"):
        raise RuntimeError("spreadsheet identity or settings changed")
    titles={s.get("properties",{}).get("title") for s in before.get("sheets",[])}
    if titles!={s.get("properties",{}).get("title") for s in after.get("sheets",[])}:
        raise RuntimeError("spreadsheet tabs changed during sync")
    for title in titles:
        bsheet=next(s for s in before["sheets"] if s.get("properties",{}).get("title")==title)
        asheet=next(s for s in after["sheets"] if s.get("properties",{}).get("title")==title)
        if bsheet.get("properties")!=asheet.get("properties"):
            raise RuntimeError("sheet layout changed during sync")
        bdata=bsheet.get("data",[]);adata=asheet.get("data",[])
        max_rows=max(len(bdata[0].get("rowData",[])) if bdata else 0,len(adata[0].get("rowData",[])) if adata else 0)
        for row in range(max_rows):
            max_cols=max(len((bdata[0].get("rowData",[])[row].get("values",[]))) if bdata and row<len(bdata[0].get("rowData",[])) else 0,
                         len((adata[0].get("rowData",[])[row].get("values",[]))) if adata and row<len(adata[0].get("rowData",[])) else 0)
            for col in range(max_cols):
                b=_cell(before,title,row,col);a=_cell(after,title,row,col)
                for key in ("userEnteredFormat","dataValidation","note"):
                    if b.get(key)!=a.get(key):raise RuntimeError("cell format or validation changed during sync")
                if title!="Applications" or (row,col) not in allowed:
                    if b.get("userEnteredValue")!=a.get("userEnteredValue"):
                        raise RuntimeError("unplanned cell value changed during sync")
                if title=="Applications" and col in FORMULA_COLUMNS:
                    if b.get("userEnteredValue")!=a.get("userEnteredValue"):
                        raise RuntimeError("formula-controlled cell changed during sync")


def _make_conflict(conn,record,row_number,fields,remote,db_values,key):
    app_id=record["application_id"]
    remote_blob=encrypt_document(_canonical(remote),key,app_id)
    db_blob=encrypt_document(_canonical(db_values),key,app_id)
    open_row=conn.execute("SELECT id FROM sheet_sync_conflict WHERE application_id=%s AND state='OPEN' ORDER BY created_at LIMIT 1",(app_id,)).fetchone()
    if open_row:
        conn.execute("UPDATE sheet_sync_conflict SET fields=%s,sheet_values_ciphertext=%s,database_values_ciphertext=%s,sheet_row=%s WHERE id=%s",
          (Jsonb(sorted(set(fields))),remote_blob,db_blob,row_number,open_row["id"]))
    else:
        conn.execute("INSERT INTO sheet_sync_conflict(id,application_id,sheet_id,sheet_row,fields,sheet_values_ciphertext,database_values_ciphertext) VALUES (%s,%s,%s,%s,%s,%s,%s)",
          (uuid4(),app_id,SPREADSHEET_ID,row_number,Jsonb(sorted(set(fields))),remote_blob,db_blob))


def _legacy_date_mismatch(mapping_exists:bool, sheet_url:Any, canonical_url:str,
                          mismatch_fields:set[int])->bool:
    """Recognize an exact-URL, first-association legacy date discrepancy."""
    return (not mapping_exists and str(sheet_url or "").strip()==canonical_url and 1 in mismatch_fields)


def run_sync(task_id:str, *, api:GoogleOAuthREST|None=None, max_records:int|None=None) -> dict[str,int]:
    if not SPREADSHEET_ID or len(SPREADSHEET_ID)>200:
        raise RuntimeError("Google Sheets tracker is not configured")
    client=api or GoogleOAuthREST(os.environ["GOOGLE_OAUTH_CREDENTIAL_FILE"])
    client._token()
    snapshot=_private_snapshot(client)
    sheet_id,rows=_worksheet(client)
    key=load_key_file(os.environ["DOCUMENT_ENCRYPTION_KEY_FILE"])
    with db.connection() as conn:
      with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",(SPREADSHEET_ID,))
        integration=conn.execute("""INSERT INTO integration(id,provider,state,credential_reference,scopes)
          VALUES (%s,'google_sheets','AUTHORIZED','/run/secrets/google_oauth',%s)
          ON CONFLICT(provider) DO UPDATE SET credential_reference=EXCLUDED.credential_reference,updated_at=now()
          RETURNING id""",(uuid4(),Jsonb(["spreadsheets"]))).fetchone()
        integration_id=integration["id"]
        conn.execute("""INSERT INTO sync_state(id,integration_id,resource,last_attempt_at,error_category)
          VALUES (%s,%s,'APPLICATIONS',now(),NULL) ON CONFLICT(integration_id,resource) DO UPDATE SET last_attempt_at=now(),error_category=NULL""",
          (uuid4(),integration_id))
        records=_records(conn)
        if max_records is not None:
            if not 1<=max_records<=50: raise ValueError("sync record bound is invalid")
        conflicts=created=updated=0
        sheet_updates=[];metadata_add=[];plans=[];allowed_changes=set();written_rows=set()
        by_url={}
        for i,row in enumerate(rows,start=2):
            url=row[13] if len(row)>13 else None
            if url not in (None,""):by_url.setdefault(str(url).strip(),[]).append(i)
        if max_records is not None:
            # Preserve the deterministic match ordering for bounded runs so an
            # immediate repeat exercises the same leading application.
            records=records[:max_records]
        for record in records:
            app_id=record["application_id"]
            mapping=conn.execute("SELECT * FROM sheet_mapping WHERE application_id=%s",(app_id,)).fetchone()
            resolved_row_decision=conn.execute("""SELECT id FROM sheet_sync_conflict WHERE application_id=%s
              AND sheet_id=%s AND state='RESOLVED' AND resolution='DATABASE_WINS'
              ORDER BY resolved_at DESC LIMIT 1""",(app_id,SPREADSHEET_ID)).fetchone()
            force_database_wins=bool((mapping and mapping.get("conflict_state")=="DATABASE_WINS") or resolved_row_decision)
            row_num,metadata_id=_metadata_row(client,app_id)
            url_rows=by_url.get(record["canonical_url"],[])
            if row_num is None and len(url_rows)>1:
                _make_conflict(conn,record,url_rows[0],["Job URL duplicate"],{"duplicate_rows":url_rows},{"canonical_url":record["canonical_url"]},key);conflicts+=1;continue
            if row_num is None and url_rows: row_num=url_rows[0]
            is_new=False
            if row_num is None:
                for idx,sheet_row in enumerate(rows,start=2):
                    padded=list(sheet_row)+[None]*max(0,32-len(sheet_row))
                    if all(padded[x] in (None,"") for x in range(32) if x not in FORMULA_COLUMNS) and all(isinstance(padded[x],str) and padded[x].startswith("=") for x in FORMULA_COLUMNS):
                        row_num=idx;is_new=True;break
                if row_num is None: raise RuntimeError("no preformatted formula row available; refusing sheet structure changes")
            if row_num<2 or row_num>len(rows)+1: raise RuntimeError("sheet row mapping is outside validated range")
            sheet_row=list(rows[row_num-2]);sheet_row.extend([None]*(32-len(sheet_row)));sheet_row=sheet_row[:32]
            occupied=conn.execute("SELECT application_id FROM sheet_mapping WHERE sheet_id=%s AND sheet_row=%s AND application_id<>%s LIMIT 1",
              (SPREADSHEET_ID,row_num,app_id)).fetchone()
            if occupied:
                _make_conflict(conn,record,row_num,["Stable row mapping"],{"row":row_num},
                  {"mapped_application_id":str(occupied["application_id"])},key);conflicts+=1;continue
            db_row=_db_row(record)
            # A stable developer-metadata pointer or URL match must still point
            # at the same canonical job before any external mutation occurs.
            if not is_new and sheet_row[13] not in (None,"") and str(sheet_row[13]).strip()!=record["canonical_url"]:
                _make_conflict(conn,record,row_num,["Job URL identity"],{"Job URL":sheet_row[13]},
                  {"Job URL":record["canonical_url"]},key);conflicts+=1;continue
            if not is_new and sheet_row[13] not in (None,"") and (sheet_row[3]!=record["company"] or sheet_row[4]!=record["title"]):
                _make_conflict(conn,record,row_num,["Company/title identity"],
                  {"Company":sheet_row[3],"Job Title":sheet_row[4]},
                  {"Company":record["company"],"Job Title":record["title"]},key);conflicts+=1;continue
            preserved=set(mapping.get("preserved_sheet_fields") or []) if mapping else set()
            # Preserve the owner's historical Date Found when first associating
            # an exact-URL legacy row, and record the exception encrypted.
            existing_open=conn.execute("SELECT id,fields FROM sheet_sync_conflict WHERE application_id=%s AND sheet_id=%s AND state='OPEN' ORDER BY created_at LIMIT 1",(app_id,SPREADSHEET_ID)).fetchone()
            legacy_date_audited=False
            if existing_open:
                fields=set(existing_open["fields"] or [])
                if fields=={"Date Found"} and not mapping:
                    conn.execute("UPDATE sheet_sync_conflict SET state='RESOLVED',resolution='LEGACY_SHEET_VALUE_PRESERVED',resolved_at=now() WHERE id=%s",(existing_open["id"],))
                    preserved.add(1)
                    legacy_date_audited=True
                else:
                    conflicts+=1;continue
            baseline=None
            if mapping and mapping.get("owner_baseline_ciphertext"):
                baseline=json.loads(decrypt_document(mapping["owner_baseline_ciphertext"],key,app_id))
            remote_conflicts=[]
            if not is_new:
                if baseline is None:
                    # Existing owner rows are never overwritten on first association.
                    remote_conflicts=[i for i in SYSTEM_OWNED if sheet_row[i] not in (None,"") and str(sheet_row[i])!=str(db_row[i])]
                else:
                    for i in SYSTEM_OWNED:
                        remote_changed=sheet_row[i]!=baseline[i]
                        db_changed=db_row[i]!=baseline[i]
                        if remote_changed and db_changed and sheet_row[i]!=db_row[i]: remote_conflicts.append(i)
                        elif remote_changed and not db_changed and sheet_row[i]!=db_row[i]: remote_conflicts.append(i)
                    # Mergeable owner fields are accepted when only the Sheet
                    # changed; concurrent database changes become conflicts.
                    for i in MERGEABLE-{16}:
                        remote_changed=sheet_row[i]!=baseline[i]
                        db_changed=db_row[i]!=baseline[i]
                        if remote_changed and db_changed and sheet_row[i]!=db_row[i]: remote_conflicts.append(i)
                if _legacy_date_mismatch(bool(mapping),sheet_row[13],record["canonical_url"],set(remote_conflicts)):
                    # Date Found on a legacy tracker row is historical owner
                    # evidence; never replace it with a later import timestamp.
                    if not legacy_date_audited:
                        _make_conflict(conn,record,row_num,["Date Found"],{"Date Found":sheet_row[1]},
                          {"Date Found":db_row[1]},key)
                        conn.execute("UPDATE sheet_sync_conflict SET state='RESOLVED',resolution='LEGACY_SHEET_VALUE_PRESERVED',resolved_at=now() WHERE application_id=%s AND sheet_id=%s AND state='OPEN'",(app_id,SPREADSHEET_ID))
                    preserved.add(1)
                    remote_conflicts.remove(1)
                remote_conflicts=[i for i in remote_conflicts if i not in preserved]
                if remote_conflicts and not force_database_wins:
                    fields=[APPLICATION_HEADERS[i] for i in remote_conflicts]
                    _make_conflict(conn,record,row_num,fields,{APPLICATION_HEADERS[i]:sheet_row[i] for i in remote_conflicts},
                      {APPLICATION_HEADERS[i]:db_row[i] for i in remote_conflicts},key);conflicts+=1;continue
            # A Sheet status is never evidence of a successful submission.
            status_value=sheet_row[16]
            status_target,status_error=_map_status(status_value,record["state"]) if not is_new and status_value not in (None,"") else (None,None)
            if status_error:
                _make_conflict(conn,record,row_num,["Status"],{"Status":status_value},{"state":record["state"]},key);conflicts+=1;continue
            owner=_owner_fields(sheet_row) if not is_new else {}
            if status_value not in (None,""):
                owner["sheet_status_claim"]=str(status_value)
            if metadata_id is None:
                metadata_add.append({"createDeveloperMetadata":{"developerMetadata":{"metadataKey":METADATA_KEY,
                  "metadataValue":str(app_id),"visibility":"DOCUMENT","location":{"dimensionRange":{"sheetId":sheet_id,
                  "dimension":"ROWS","startIndex":row_num-1,"endIndex":row_num}}}}})
            for idx in sorted(SYSTEM_OWNED|MERGEABLE):
                if idx in FORMULA_COLUMNS or idx in preserved:continue
                value=db_row[idx]
                if idx==16 and not is_new and sheet_row[idx] not in (None,"") and not force_database_wins:
                    continue
                if value is not None and str(sheet_row[idx])!=str(value):
                    cell_range=f"'Applications'!{_col(idx+1)}{row_num}"
                    sheet_updates.append({"range":cell_range,"values":[[value]]})
                    allowed_changes.add((row_num-1,idx))
                    written_rows.add(row_num)
            if not mapping and not is_new and str(sheet_row[13]).strip()==record["canonical_url"]:
                # A sparse RAW write of the already-verified canonical URL is
                # a harmless first-association write probe. It cannot alter a
                # row's content, and proves the production write path before
                # reconciliation proceeds to the other existing rows.
                cell_range=f"'Applications'!N{row_num}"
                if not any(update["range"]==cell_range for update in sheet_updates):
                    sheet_updates.append({"range":cell_range,"values":[[record["canonical_url"]]]})
                    allowed_changes.add((row_num-1,13))
            plans.append((record,row_num,metadata_id,owner,sheet_row,db_row,status_target,is_new))
        if metadata_add:
            client.batch_update_requests(spreadsheet_id=SPREADSHEET_ID,requests=metadata_add)
            time.sleep(0.2)
        if sheet_updates:
            # sparse value ranges only; formula, format and validation cells are never in the payload
            client.batch_update(spreadsheet_id=SPREADSHEET_ID,data=sheet_updates,value_input_option="RAW")
        time.sleep(0.2)
        fresh=client.get(spreadsheet_id=SPREADSHEET_ID,range="'Applications'!A1:AF1000",value_render_option="FORMULA")
        if not fresh or tuple(fresh[0])!=APPLICATION_HEADERS:raise RuntimeError("tracker header changed during synchronization")
        after_export=client.spreadsheet_export(spreadsheet_id=SPREADSHEET_ID)
        _verify_preservation(snapshot["export"],after_export,allowed_changes)
        fresh_rows=fresh[1:]
        for record,row_num,metadata_id,owner,old_row,db_row,status_target,is_new in plans:
            current=list(fresh_rows[row_num-2]) if row_num-2<len(fresh_rows) else []
            current.extend([None]*(32-len(current)));current=current[:32]
            if str(current[13])!=str(record["canonical_url"]) or current[3]!=record["company"] or current[4]!=record["title"]:
                raise RuntimeError("row identity read-back verification failed")
            app_id=record["application_id"]
            if status_target and status_target!=record["state"]:
                conn.execute("UPDATE application SET state=%s,updated_at=now() WHERE id=%s",(status_target,app_id))
                conn.execute("INSERT INTO application_event(id,application_id,from_state,to_state,actor,evidence) VALUES (%s,%s,%s,%s,'google-sheet-owner-edit',%s)",
                  (uuid4(),app_id,record["state"],status_target,Jsonb({"source":"owner-edited Sheet status","submission_verified":False})))
            encrypted=encrypt_document(_canonical(owner),key,app_id)
            conn.execute("""INSERT INTO sheet_owner_state(application_id,fields_ciphertext,updated_at) VALUES (%s,%s,now())
              ON CONFLICT(application_id) DO UPDATE SET fields_ciphertext=EXCLUDED.fields_ciphertext,updated_at=now()""",(app_id,encrypted))
            baseline_cipher=encrypt_document(_canonical(current),key,app_id)
            sheet_hash=row_hash(current);database_hash=row_hash(db_row)
            if mapping:=conn.execute("SELECT id FROM sheet_mapping WHERE application_id=%s",(app_id,)).fetchone():
                conn.execute("""UPDATE sheet_mapping SET sheet_id=%s,sheet_row=%s,visible_application_id=%s,
                  last_sheet_hash=%s,last_database_hash=%s,last_synced_at=now(),conflict_state=NULL,
                  owner_baseline_ciphertext=%s,developer_metadata_id=%s,
                  preserved_sheet_fields=%s WHERE application_id=%s""",
                  (SPREADSHEET_ID,row_num,current[0],sheet_hash,database_hash,baseline_cipher,metadata_id,
                   sorted(preserved),app_id))
                conn.execute("UPDATE application SET sheet_row=%s,sheet_visible_id=%s WHERE id=%s",(row_num,current[0],app_id))
                if row_num in written_rows: updated+=1
            else:
                conn.execute("""INSERT INTO sheet_mapping(id,application_id,sheet_id,sheet_row,visible_application_id,
                  last_sheet_hash,last_database_hash,last_synced_at,owner_baseline_ciphertext,developer_metadata_id,preserved_sheet_fields)
                  VALUES (%s,%s,%s,%s,%s,%s,%s,now(),%s,%s,%s)""",
                  (uuid4(),app_id,SPREADSHEET_ID,row_num,current[0],sheet_hash,database_hash,baseline_cipher,metadata_id,sorted(preserved)))
                created+=1
                conn.execute("UPDATE application SET sheet_row=%s,sheet_visible_id=%s WHERE id=%s",(row_num,current[0],app_id))
            if force_database_wins and resolved_row_decision:
                conn.execute("UPDATE sheet_sync_conflict SET resolution='DATABASE_WINS_APPLIED' WHERE id=%s",(resolved_row_decision["id"],))
        conn.execute("""INSERT INTO sync_state(id,integration_id,resource,last_success_at,last_attempt_at,retry_count,dead_letter_count,error_category)
          VALUES (%s,%s,'APPLICATIONS',now(),now(),0,0,NULL)
          ON CONFLICT(integration_id,resource) DO UPDATE SET last_success_at=now(),last_attempt_at=now(),retry_count=0,error_category=NULL""",
          (uuid4(),integration_id))
        conn.execute("UPDATE integration SET state='CONNECTED',last_success_at=now(),last_error_category=NULL,updated_at=now() WHERE id=%s",(integration_id,))
        conn.execute("UPDATE agent_configuration SET enabled=true,paused=false,drain=false,updated_at=now() WHERE agent='sheet_sync'")
        conn.execute("INSERT INTO audit_event(id,actor,action,object_type,request_id,result,metadata) VALUES (%s,'sheets-worker','GOOGLE_SHEET_SYNC','integration',%s,'SUCCEEDED',%s)",
          (uuid4(),UUID(task_id) if _is_uuid(task_id) else uuid4(),Jsonb({"rows_created":created,"rows_updated":updated,"conflicts":conflicts,"snapshot_refreshed":snapshot["refreshed"]})))
        created=sum(1 for plan in plans if plan[7])
        updated=sum(1 for plan in plans if not plan[7] and plan[1] in written_rows)
        return {"rows_created":created,"rows_updated":updated,"conflicts":conflicts,"failed_jobs":0}


def _is_uuid(value:str)->bool:
    try:UUID(value);return True
    except Exception:return False


def _col(n:int)->str:
    result=""
    while n:n,r=divmod(n-1,26);result=chr(65+r)+result
    return result
