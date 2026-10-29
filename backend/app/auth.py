from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import os

from fastapi import HTTPException, Request

from . import config


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
    return jwt.PyJWKClient(config.ACCESS_ISSUER + "/cdn-cgi/access/certs", cache_keys=True,
                           lifespan=3600, timeout=3)


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
