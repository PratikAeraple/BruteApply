import json

import pytest

from app.integrations.access_jwks import fetch_access_jwks, validate_issuer, validate_jwks


def jwk():
    return {"kty":"RSA","kid":"kid-1","use":"sig","alg":"RS256","n":"AQAB","e":"AQAB",
            "d":"must-not-be-persisted"}


def test_access_issuer_is_https_cloudflare_team_only():
    assert validate_issuer("https://team.cloudflareaccess.com").endswith("/cdn-cgi/access/certs")
    for issuer in ("http://team.cloudflareaccess.com", "https://cloudflareaccess.com.evil.test",
                   "https://user@team.cloudflareaccess.com", "https://team.cloudflareaccess.com:8443"):
        with pytest.raises(ValueError):
            validate_issuer(issuer)


def test_jwks_rejects_private_signing_material():
    with pytest.raises(ValueError, match="invalid public signing key"):
        validate_jwks({"keys":[jwk()]})
    result=validate_jwks({"keys":[{k:v for k,v in jwk().items() if k!="d"}]})
    assert result=={"keys":[{"kty":"RSA","kid":"kid-1","use":"sig","alg":"RS256","n":"AQAB","e":"AQAB"}]}
    with pytest.raises(ValueError):
        validate_jwks({"keys":[jwk(),jwk()]})


def test_fetch_is_bounded_and_does_not_follow_redirects():
    class Response:
        status=200
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def read(self,limit):
            assert limit==65537
            return json.dumps({"keys":[{k:v for k,v in jwk().items() if k!="d"}]}).encode()
    class Opener:
        def open(self,request,timeout):
            assert request.full_url=="https://team.cloudflareaccess.com/cdn-cgi/access/certs"
            assert timeout==5
            return Response()
    assert fetch_access_jwks("https://team.cloudflareaccess.com",opener=Opener())["keys"][0]["kid"]=="kid-1"


def test_fetch_rejects_redirect_and_oversize():
    class Redirecting:
        def open(self,*args,**kwargs): raise RuntimeError("redirects disabled")
    with pytest.raises(RuntimeError):
        fetch_access_jwks("https://team.cloudflareaccess.com",opener=Redirecting())
    class Oversize:
        status=200
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def read(self,limit): return b"x"*65537
    class OversizeOpener:
        def open(self,*args,**kwargs): return Oversize()
    with pytest.raises(ValueError,match="size limit"):
        fetch_access_jwks("https://team.cloudflareaccess.com",opener=OversizeOpener())
