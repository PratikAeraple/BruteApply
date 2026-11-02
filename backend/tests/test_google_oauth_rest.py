from pathlib import Path
import json

import httpx
import pytest
from urllib.parse import parse_qs, urlsplit

from app.integrations.google_oauth_rest import GOOGLE_SCOPES, GoogleOAuthREST
from scripts.google_oauth_local import (_authorization_url, _default_output_path,
    _read_client, _validate_granted_scopes, _verify_access_token_scopes, _exchange)


def _credential_file(tmp_path: Path) -> Path:
    path=tmp_path/"google-oauth.json"
    path.write_text('{"client_id":"test-client","client_secret":"fake-secret",'
                    '"refresh_token":"fake-refresh"}',encoding="utf-8")
    path.chmod(0o600)
    return path


def test_google_rest_refreshes_token_and_reads_formula_values(tmp_path):
    calls=[]
    def handler(request):
        calls.append(request)
        if request.url.host=="oauth2.googleapis.com":
            return httpx.Response(200,json={"access_token":"fake-access","expires_in":3600})
        assert request.url.host=="sheets.googleapis.com"
        assert request.headers["Authorization"]=="Bearer fake-access"
        return httpx.Response(200,json={"values":[["Application ID","Date Found"]]})
    api=GoogleOAuthREST(str(_credential_file(tmp_path)),transport=httpx.MockTransport(handler))
    assert api.get(spreadsheet_id="sheet-id",range="'Applications'!A1:B1")==[["Application ID","Date Found"]]
    assert len(calls)==2


def test_google_rest_rejects_credential_with_broad_permissions(tmp_path):
    path=_credential_file(tmp_path);path.chmod(0o644)
    with pytest.raises(RuntimeError,match="credential file"):
        GoogleOAuthREST(str(path))


def test_google_rest_allows_group_readable_owner_runtime_secret(tmp_path):
    path=_credential_file(tmp_path);path.chmod(0o640)
    assert GoogleOAuthREST(str(path),transport=httpx.MockTransport(lambda _:httpx.Response(500)))


def test_google_rest_redacts_provider_error_body(tmp_path):
    def handler(request):
        if request.url.host=="oauth2.googleapis.com":
            return httpx.Response(200,json={"access_token":"fake-access","expires_in":3600})
        return httpx.Response(403,text="echo fake-refresh and private candidate data")
    api=GoogleOAuthREST(str(_credential_file(tmp_path)),transport=httpx.MockTransport(handler),sleep=lambda _:None)
    with pytest.raises(RuntimeError) as error:
        api.get(spreadsheet_id="sheet-id",range="'Applications'!A1:AF1000")
    assert "fake-refresh" not in str(error.value)
    assert "private candidate data" not in str(error.value)


def test_google_rest_requires_raw_value_writes_to_block_formula_injection(tmp_path):
    seen=[]
    def handler(request):
        if request.url.host=="oauth2.googleapis.com":
            return httpx.Response(200,json={"access_token":"fake-access","expires_in":3600})
        seen.append(request)
        return httpx.Response(200,json={"totalUpdatedCells":1})
    api=GoogleOAuthREST(str(_credential_file(tmp_path)),transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError,match="RAW"):
        api.batch_update(spreadsheet_id="sheet-id",data=[{"range":"'Applications'!D2","values":[["=IMPORTXML()"]]}],value_input_option="USER_ENTERED")
    api.batch_update(spreadsheet_id="sheet-id",data=[{"range":"'Applications'!D2","values":[["=IMPORTXML()"]]}],value_input_option="RAW")
    body=json.loads(seen[-1].content)
    assert body["valueInputOption"]=="RAW"
    assert body["data"][0]["values"]==[["=IMPORTXML()"]]


def test_google_rest_uses_documented_developer_metadata_search_route(tmp_path):
    seen=[]
    def handler(request):
        if request.url.host=="oauth2.googleapis.com":
            return httpx.Response(200,json={"access_token":"fake-access","expires_in":3600})
        seen.append(request)
        return httpx.Response(200,json={"matchedDeveloperMetadata":[]})
    api=GoogleOAuthREST(str(_credential_file(tmp_path)),transport=httpx.MockTransport(handler))
    assert api.search_developer_metadata(spreadsheet_id="sheet-id",key="uuid",value="app-id")==[]
    assert seen[0].url.path.endswith("/developerMetadata:search")


def test_oauth_scope_is_sheets_only_and_covers_developer_metadata():
    assert GOOGLE_SCOPES==("https://www.googleapis.com/auth/spreadsheets",)


def test_google_rest_refuses_non_sheets_origin_before_token_or_request(tmp_path):
    calls=[]
    api=GoogleOAuthREST(str(_credential_file(tmp_path)),transport=httpx.MockTransport(
        lambda request:(calls.append(request),httpx.Response(200,json={"values":[]}))[1]))
    with pytest.raises(RuntimeError,match="origin validation"):
        api._request("GET","https://www.googleapis.com/drive/v3/files/file-id")
    assert calls==[]


def test_local_oauth_default_uses_separate_non_overwriting_output(tmp_path):
    path=_default_output_path(tmp_path)
    assert path.name=="google-oauth-sheets-only.json"
    assert path!=tmp_path/".local/state/bruteapply/google-oauth.json"


def test_local_oauth_authorization_requests_only_sheets_scope():
    url=_authorization_url("client.apps.googleusercontent.com","state","challenge")
    query=parse_qs(urlsplit(url).query)
    assert query["scope"]==["https://www.googleapis.com/auth/spreadsheets"]
    assert query["access_type"]==["offline"]
    assert query["include_granted_scopes"]==["false"]
    assert query["code_challenge_method"]==["S256"]


def test_local_oauth_scope_validation_rejects_broader_or_missing_grant():
    _validate_granted_scopes({"scope":GOOGLE_SCOPES[0]})
    with pytest.raises(RuntimeError,match="required Sheets scope"):
        _validate_granted_scopes({"scope":GOOGLE_SCOPES[0]+" https://www.googleapis.com/auth/drive.file"})
    with pytest.raises(RuntimeError,match="required Sheets scope"):
        _validate_granted_scopes({"scope":"https://www.googleapis.com/auth/drive.file"})
    with pytest.raises(RuntimeError,match="required Sheets scope"):
        _validate_granted_scopes({})


def test_local_oauth_exchange_scope_validation_is_mocked_and_private():
    def handler(request):
        assert request.url.host=="oauth2.googleapis.com"
        if request.url.path=="/token":
            assert "client_secret=secret" not in request.url.query.decode()
            return httpx.Response(200,json={"refresh_token":"fake-refresh","access_token":"fake-access"})
        assert request.url.path=="/tokeninfo"
        assert "fake-access" not in request.url.query.decode()
        assert b"fake-access" in request.content
        return httpx.Response(200,json={"scope":"https://www.googleapis.com/auth/spreadsheets"})
    assert _exchange("fake-code","verifier","client-id","secret",
      transport=httpx.MockTransport(handler))=="fake-refresh"


def test_local_oauth_exchange_does_not_accept_unexpected_scope():
    def handler(request):
        if request.url.path=="/token":
            return httpx.Response(200,json={"refresh_token":"fake-refresh","access_token":"fake-access"})
        return httpx.Response(200,json={"scope":"https://www.googleapis.com/auth/spreadsheets https://www.googleapis.com/auth/drive.file"})
    with pytest.raises(RuntimeError,match="required Sheets scope") as exc:
        _exchange("fake-code","verifier","client-id","secret",transport=httpx.MockTransport(handler))
    assert "fake-access" not in str(exc.value)


def test_token_scope_verification_rejects_missing_actual_grant():
    with pytest.raises(RuntimeError,match="required Sheets scope"):
        _verify_access_token_scopes("fake-access",transport=httpx.MockTransport(
            lambda _:httpx.Response(200,json={})))


def test_oauth_client_requires_private_file_and_loopback_redirect(tmp_path):
    path=tmp_path/"oauth-client.json"
    path.write_text('{"installed":{"client_id":"demo.apps.googleusercontent.com",'
      '"client_secret":"local-only","redirect_uris":["http://127.0.0.1:8765/callback"]}}')
    path.chmod(0o600)
    assert _read_client(path)==("demo.apps.googleusercontent.com","local-only")
    path.chmod(0o644)
    with pytest.raises(RuntimeError,match="private regular"):
        _read_client(path)
