from pathlib import Path

import httpx
import pytest

from app.integrations.google_oauth_rest import GoogleOAuthREST
from scripts.google_oauth_local import _read_client


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


def test_oauth_client_requires_private_file_and_loopback_redirect(tmp_path):
    path=tmp_path/"oauth-client.json"
    path.write_text('{"installed":{"client_id":"demo.apps.googleusercontent.com",'
      '"client_secret":"local-only","redirect_uris":["http://127.0.0.1:8765/callback"]}}')
    path.chmod(0o600)
    assert _read_client(path)==("demo.apps.googleusercontent.com","local-only")
    path.chmod(0o644)
    with pytest.raises(RuntimeError,match="private regular"):
        _read_client(path)
