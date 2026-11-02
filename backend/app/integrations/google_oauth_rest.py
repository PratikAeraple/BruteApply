"""Google Sheets REST transport using an owner-authorized OAuth refresh file.

Only fixed Google API origins are contacted. The credential file is private,
runtime-only, and its contents are never included in exceptions or logs.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import stat
import time
from typing import Any
from urllib.parse import quote

import httpx


GOOGLE_SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets",
)
OAUTH_HOST = "oauth2.googleapis.com"
API_HOST = "sheets.googleapis.com"

# httpx's default INFO request logger records full URLs, including the private
# spreadsheet identifier. Keep OAuth and Sheets request URLs out of logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


class GoogleOAuthREST:
    def __init__(self, credential_file: str, *, transport: httpx.BaseTransport | None = None,
                 clock=lambda: datetime.now(timezone.utc), sleep=time.sleep):
        path=Path(credential_file)
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("Google OAuth credential file is unavailable or too broadly readable")
        info=path.stat(); mode=stat.S_IMODE(info.st_mode)
        if mode not in {0o400,0o600,0o640} or (info.st_uid!=os.getuid() and info.st_gid not in os.getgroups()):
            raise RuntimeError("Google OAuth credential file is unavailable or too broadly readable")
        try:
            credentials=json.loads(path.read_text(encoding="utf-8"))
            if set(credentials)!={"client_id","client_secret","refresh_token"} or any(
                not isinstance(credentials[key],str) or not credentials[key] for key in credentials
            ):
                raise ValueError
        except Exception:
            raise RuntimeError("Google OAuth credential file format is invalid") from None
        self._client_id=credentials["client_id"]
        self._client_secret=credentials["client_secret"]
        self._refresh_token=credentials["refresh_token"]
        self._transport=transport
        self._clock=clock
        self._sleep=sleep
        self._access_token: str | None=None
        self._expires_at=datetime.min.replace(tzinfo=timezone.utc)

    def _token(self) -> str:
        now=self._clock()
        if self._access_token and now+timedelta(seconds=30)<self._expires_at:
            return self._access_token
        with httpx.Client(timeout=httpx.Timeout(12,connect=4),follow_redirects=False,transport=self._transport) as client:
            try:
                response=client.post(f"https://{OAUTH_HOST}/token",data={
                    "client_id":self._client_id,"client_secret":self._client_secret,
                    "refresh_token":self._refresh_token,"grant_type":"refresh_token"})
                if response.url.host!=OAUTH_HOST or response.url.scheme!="https":
                    raise RuntimeError("Google OAuth origin validation failed")
                response.raise_for_status()
                payload=response.json()
                token=payload.get("access_token")
                lifetime=int(payload.get("expires_in",0))
                if not isinstance(token,str) or not token or lifetime<60:
                    raise RuntimeError("Google OAuth did not return a usable access token")
            except RuntimeError:
                raise
            except Exception:
                raise RuntimeError("Google OAuth token refresh failed") from None
        self._access_token=token
        self._expires_at=now+timedelta(seconds=lifetime)
        return token

    def _request(self, method: str, url: str, *, json_body: dict | None = None,
                 params: dict[str,str] | None = None, retries: int = 3) -> dict[str,Any]:
        # Validate before attaching the bearer token. The production credential
        # may still carry its historical Drive grant until a separate rotation.
        parsed=httpx.URL(url)
        if parsed.scheme!="https" or parsed.host!=API_HOST or parsed.port not in {None,443} or parsed.username or parsed.password:
            raise RuntimeError("Google API origin validation failed")
        for attempt in range(retries):
            with httpx.Client(timeout=httpx.Timeout(20,connect=5),follow_redirects=False,transport=self._transport) as client:
                try:
                    response=client.request(method,url,headers={"Authorization":"Bearer "+self._token(),
                        "Accept":"application/json"},json=json_body,params=params)
                    if response.url.scheme!="https" or response.url.host!=API_HOST:
                        raise RuntimeError("Google API origin validation failed")
                    if response.status_code in {429,500,502,503,504} and attempt+1<retries:
                        self._sleep(min(2**attempt,4))
                        continue
                    response.raise_for_status()
                    return response.json()
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in {429,500,502,503,504} and attempt+1<retries:
                        self._sleep(min(2**attempt,4))
                        continue
                    raise RuntimeError("Google API request failed") from None
                except RuntimeError:
                    raise
                except (httpx.TimeoutException,httpx.RequestError):
                    if attempt+1<retries:
                        self._sleep(min(2**attempt,4))
                        continue
                    # Google may echo user-entered sheet data in error details.
                    raise RuntimeError("Google API request failed") from None
                except Exception:
                    raise RuntimeError("Google API response was invalid") from None
        raise RuntimeError("Google API request failed")

    def get(self, *, spreadsheet_id: str, range: str,
            value_render_option: str = "FORMULA") -> list[list[Any]]:
        if not spreadsheet_id or len(spreadsheet_id)>200:
            raise ValueError("spreadsheet identity is invalid")
        encoded_range=quote(range,safe="'!:$")
        payload=self._request("GET",f"https://{API_HOST}/v4/spreadsheets/{quote(spreadsheet_id,safe='')}/values/{encoded_range}",
                              params={"valueRenderOption":value_render_option})
        values=payload.get("values",[])
        if not isinstance(values,list) or any(not isinstance(row,list) for row in values):
            raise RuntimeError("Google Sheets returned an invalid values response")
        return values

    def spreadsheet_export(self, *, spreadsheet_id: str) -> dict[str, Any]:
        if not spreadsheet_id or len(spreadsheet_id)>200:
            raise ValueError("spreadsheet identity is invalid")
        return self._request("GET",f"https://{API_HOST}/v4/spreadsheets/{quote(spreadsheet_id,safe='')}",
          params={"includeGridData":"true","ranges":["Applications","Dashboard","Lists"],
            "fields":"spreadsheetId,properties,sheets(properties,data(rowData(values(userEnteredValue,effectiveValue,formattedValue,userEnteredFormat,dataValidation,note))))"})

    def spreadsheet_metadata(self, *, spreadsheet_id: str) -> dict[str, Any]:
        if not spreadsheet_id or len(spreadsheet_id)>200:
            raise ValueError("spreadsheet identity is invalid")
        return self._request("GET",f"https://{API_HOST}/v4/spreadsheets/{quote(spreadsheet_id,safe='')}",
          params={"includeGridData":"false","fields":"spreadsheetId,properties(title,timeZone),sheets(properties(sheetId,title,index,gridProperties(rowCount,columnCount)))"})

    def batch_update(self, *, spreadsheet_id: str, data: list[dict[str,Any]],
                     value_input_option: str) -> None:
        if not data or len(data)>500:
            raise ValueError("Google Sheets update batch is outside the safe size limit")
        # External job and owner text is untrusted. RAW prevents values beginning
        # with '=' from being interpreted as formulas in the owner's tracker.
        if value_input_option != "RAW":
            raise ValueError("Google Sheets writes must use RAW input mode")
        self._request("POST",f"https://{API_HOST}/v4/spreadsheets/{quote(spreadsheet_id,safe='')}/values:batchUpdate",
                      json_body={"valueInputOption":"RAW","data":data},retries=1)

    def batch_update_requests(self, *, spreadsheet_id: str, requests: list[dict[str,Any]]) -> dict[str,Any]:
        if not spreadsheet_id or len(spreadsheet_id)>200 or not requests or len(requests)>500:
            raise ValueError("Sheets structural update is outside safe bounds")
        return self._request("POST",f"https://{API_HOST}/v4/spreadsheets/{quote(spreadsheet_id,safe='')}:batchUpdate",
                             json_body={"requests":requests},retries=1)

    def search_developer_metadata(self, *, spreadsheet_id: str, key: str, value: str) -> list[dict[str,Any]]:
        if not spreadsheet_id or len(spreadsheet_id)>200 or not key or len(key)>100 or len(value)>200:
            raise ValueError("developer metadata lookup is invalid")
        response=self._request("POST",f"https://{API_HOST}/v4/spreadsheets/{quote(spreadsheet_id,safe='')}/developerMetadata:search",
          json_body={"dataFilters":[{"developerMetadataLookup":{"metadataKey":key,"metadataValue":value,"visibility":"DOCUMENT"}}]},retries=1)
        matches=response.get("matchedDeveloperMetadata",[])
        if not isinstance(matches,list):
            raise RuntimeError("Google Sheets metadata response is invalid")
        return matches
