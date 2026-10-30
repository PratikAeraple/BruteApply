#!/usr/bin/env python3
"""Owner-attended loopback OAuth bootstrap for BruteApply's Google connector.

The downloaded OAuth client file and resulting refresh credential are private
runtime artifacts. Neither is read from command-line text or printed.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import stat
import sys
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from webbrowser import open as open_browser

from app.integrations.google_oauth_rest import GOOGLE_SCOPES, OAUTH_HOST


REDIRECT_URI="http://127.0.0.1:8765/callback"


class CallbackHandler(BaseHTTPRequestHandler):
    server_version="BruteApplyOAuth/1.0"
    sys_version=""

    def do_GET(self):
        parsed=urlsplit(self.path)
        query=parse_qs(parsed.query)
        if parsed.path!="/callback" or query.get("state",[None])[0]!=self.server.oauth_state:
            self.send_error(400,"Authorization state could not be verified")
            return
        self.server.oauth_result={"code":query.get("code",[None])[0],
                                  "error":query.get("error",[None])[0]}
        self.send_response(200,"Authorization received")
        self.send_header("Content-Type","text/plain; charset=utf-8")
        self.send_header("Cache-Control","no-store")
        self.end_headers()
        self.wfile.write(b"BruteApply authorization received. You may close this tab and return to the local terminal.")

    def log_message(self,format,*args):
        # The HTTP request query carries a short-lived authorization code.
        return


def _read_client(path: Path) -> tuple[str,str]:
    if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode)&0o077:
        raise RuntimeError("OAuth client file must be a private regular file")
    try:
        payload=json.loads(path.read_text(encoding="utf-8"))
        config=payload.get("installed") or payload.get("web")
        client_id=config["client_id"]
        client_secret=config["client_secret"]
        if not isinstance(client_id,str) or not client_id.endswith(".apps.googleusercontent.com"):
            raise ValueError
        if not isinstance(client_secret,str) or not client_secret:
            raise ValueError
    except Exception:
        raise RuntimeError("OAuth client file format is invalid") from None
    redirects=config.get("redirect_uris",[])
    if "http://127.0.0.1:8765/callback" not in redirects and "http://localhost:8765/callback" not in redirects:
        raise RuntimeError("register the loopback callback URI on the Google OAuth client")
    return client_id,client_secret


def _exchange(code: str, verifier: str, client_id: str, client_secret: str) -> str:
    request_body=urlencode({"code":code,"client_id":client_id,"client_secret":client_secret,
      "redirect_uri":REDIRECT_URI,"grant_type":"authorization_code","code_verifier":verifier})
    try:
        with httpx.Client(timeout=httpx.Timeout(15,connect=5),follow_redirects=False) as client:
            response=client.post(f"https://{OAUTH_HOST}/token",data=request_body)
            if response.url.scheme!="https" or response.url.host!=OAUTH_HOST:
                raise RuntimeError("Google token endpoint origin validation failed")
            response.raise_for_status()
            payload=response.json()
        refresh=payload.get("refresh_token")
        if not isinstance(refresh,str) or not refresh:
            raise RuntimeError("Google did not issue offline authorization; no credential was saved")
        return refresh
    except RuntimeError:
        raise
    except Exception:
        raise RuntimeError("Google OAuth exchange failed; response details are withheld") from None


def main() -> int:
    parser=argparse.ArgumentParser(description="Authorize BruteApply's Google Sheets connector through a private local browser flow")
    parser.add_argument("--oauth-client-file",required=True,type=Path)
    parser.add_argument("--output",type=Path,default=Path.home()/".local/state/bruteapply/google-oauth.json")
    args=parser.parse_args()
    try:
        client_id,client_secret=_read_client(args.oauth_client_file)
        if args.output.exists() or args.output.is_symlink():
            raise RuntimeError("credential output already exists; remove it deliberately before rotating")
        args.output.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        os.chmod(args.output.parent,0o700)
        state=secrets.token_urlsafe(32)
        verifier=base64.urlsafe_b64encode(secrets.token_bytes(48)).decode().rstrip("=")
        challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        query=urlencode({"client_id":client_id,"redirect_uri":REDIRECT_URI,"response_type":"code",
          "scope":" ".join(GOOGLE_SCOPES),"access_type":"offline","prompt":"consent",
          "state":state,"code_challenge":challenge,"code_challenge_method":"S256"})
        authorize_url=f"https://accounts.google.com/o/oauth2/v2/auth?{query}"
        server=HTTPServer(("127.0.0.1",8765),CallbackHandler)
        server.oauth_state=state;server.oauth_result=None
        print("A Google authorization page will open in your browser. No password or credential is requested by this script.")
        open_browser(authorize_url)
        server.timeout=240
        server.handle_request()
        result=server.oauth_result
        server.server_close()
        if not result or result.get("error") or not result.get("code"):
            raise RuntimeError("Google authorization was not completed")
        refresh=_exchange(result["code"],verifier,client_id,client_secret)
        fd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as stream:
            json.dump({"client_id":client_id,"client_secret":client_secret,"refresh_token":refresh},stream)
            stream.write("\n");stream.flush();os.fsync(stream.fileno())
        print(f"Google OAuth credential created with mode 0600: {args.output}")
        print("No token value was displayed. The authorized API scopes are listed in BruteApply documentation.")
        return 0
    except Exception as exc:
        print(f"Google OAuth setup failed: {exc}",file=sys.stderr)
        return 1


if __name__=="__main__":
    raise SystemExit(main())
