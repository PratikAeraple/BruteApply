from datetime import datetime, timedelta, timezone
import asyncio

from fastapi import HTTPException
from starlette.requests import Request

from app import auth, config


def make_request(token: str | None) -> Request:
    headers=[] if token is None else [(b"cf-access-jwt-assertion",token.encode())]
    return Request({"type":"http","asgi":{"version":"3.0"},"http_version":"1.1","method":"GET",
        "scheme":"https","path":"/","raw_path":b"/","query_string":b"","headers":headers,
        "client":("127.0.0.1",1234),"server":("jobapply.brutehack.com",443)})


def test_missing_access_assertion_fails_closed(monkeypatch):
    monkeypatch.setattr(config,"DEMO_MODE",False)
    try:
        asyncio.run(auth.require_owner(make_request(None)))
        assert False,"expected rejection"
    except HTTPException as exc:
        assert exc.status_code == 401


def test_signed_access_assertion_requires_exact_identity(monkeypatch):
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    public=private.public_key()
    monkeypatch.setattr(config,"DEMO_MODE",False)
    monkeypatch.setattr(config,"ACCESS_ISSUER","https://team.cloudflareaccess.com")
    monkeypatch.setattr(config,"ACCESS_AUD","bruteapply-audience")
    monkeypatch.setattr(config,"OWNER_EMAIL","owner@example.com")
    class Key:
        key=public
    class Client:
        def get_signing_key_from_jwt(self,token): return Key()
    monkeypatch.setattr(auth,"_jwk_client",lambda:Client())
    claims={"iss":config.ACCESS_ISSUER,"aud":[config.ACCESS_AUD],"sub":"owner-subject",
            "email":"owner@example.com","iat":datetime.now(timezone.utc),"exp":datetime.now(timezone.utc)+timedelta(minutes=2)}
    token=jwt.encode(claims,private,algorithm="RS256")
    principal=asyncio.run(auth.require_owner(make_request(token)))
    assert principal.email == "owner@example.com"
    bad=jwt.encode({**claims,"email":"other@example.com"},private,algorithm="RS256")
    try:
        asyncio.run(auth.require_owner(make_request(bad)))
        assert False,"expected owner mismatch rejection"
    except HTTPException as exc:
        assert exc.status_code == 401
