from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
import os

from fastapi import HTTPException, Request

from . import config, db


@dataclass(frozen=True)
class Principal:
    email: str
    subject: str
    demo: bool = False


@lru_cache(maxsize=1)
def _jwk_client():
    import jwt
    if not config.ACCESS_ISSUER:
        raise RuntimeError("Access issuer missing")

    class CachedAccessKeyClient:
        """Use the short-lived public JWKS cache; never give the API egress."""
        def get_signing_key_from_jwt(self, token):
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
                raise ValueError("unsupported Access signing key")
            with db.connection() as conn:
                row = conn.execute("""SELECT jwks FROM access_jwks_cache
                  WHERE id=1 AND fetched_at > now()-interval '24 hours'""").fetchone()
            if not row or not isinstance(row["jwks"], dict):
                raise RuntimeError("Access signing-key cache is stale or unavailable")
            jwk = next((item for item in row["jwks"].get("keys", [])
                        if item.get("kid") == header["kid"]), None)
            if not jwk:
                raise RuntimeError("Access signing key is not in the current cache")
            key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
            return type("SigningKey", (), {"key": key})()

    return CachedAccessKeyClient()


async def require_owner(request: Request) -> Principal:
    if config.DEMO_MODE:
        return Principal("demo@example.invalid", "synthetic-demo", True)
    assertion = request.headers.get("cf-access-jwt-assertion")
    if not assertion:
        raise HTTPException(401, "authentication required")
    try:
        import jwt
        key = _jwk_client().get_signing_key_from_jwt(assertion)
        claims = jwt.decode(assertion, key.key, algorithms=["RS256"], audience=config.ACCESS_AUD,
                            issuer=config.ACCESS_ISSUER, options={"require": ["exp", "iat", "iss", "aud"]})
        email = str(claims.get("email", "")).casefold()
        subject = str(claims.get("sub", ""))
        if not subject or email != config.OWNER_EMAIL:
            raise ValueError("owner identity mismatch")
        return Principal(email, subject)
    except Exception:
        raise HTTPException(401, "authentication required") from None
