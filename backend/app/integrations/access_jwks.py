"""Fetch and cache only public Cloudflare Access signing keys.

This module is called by the egress-enabled worker. The owner API is on
internal-only Docker networks and reads the cached JWKS from PostgreSQL.
"""
from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def validate_issuer(issuer: str) -> str:
    parsed = urlsplit(issuer)
    if (parsed.scheme != "https" or not parsed.hostname or
        not parsed.hostname.endswith(".cloudflareaccess.com") or
        parsed.username or parsed.password or parsed.port or parsed.path not in ("", "/") or
        parsed.query or parsed.fragment):
        raise ValueError("invalid Cloudflare Access issuer")
    return f"{issuer.rstrip('/')}/cdn-cgi/access/certs"


def validate_jwks(value: object) -> dict:
    if not isinstance(value, dict) or not isinstance(value.get("keys"), list):
        raise ValueError("invalid JWKS document")
    keys = value["keys"]
    if not 1 <= len(keys) <= 32:
        raise ValueError("invalid JWKS key count")
    valid = []
    kids = set()
    for key in keys:
        if not isinstance(key, dict):
            raise ValueError("invalid JWK")
        kid = key.get("kid")
        if (not isinstance(kid, str) or not kid or len(kid) > 256 or kid in kids or
            key.get("kty") != "RSA" or key.get("alg") not in (None, "RS256") or
            key.get("use") not in (None, "sig") or not isinstance(key.get("n"), str) or
            not isinstance(key.get("e"), str) or any(name in key for name in ("d", "p", "q", "dp", "dq", "qi", "oth"))):
            raise ValueError("invalid public signing key")
        kids.add(kid)
        # Persist only fields needed for public signature verification.
        valid.append({name: key[name] for name in ("kty", "kid", "use", "alg", "n", "e") if name in key})
    return {"keys": valid}


def fetch_access_jwks(issuer: str, *, opener=None) -> dict:
    url = validate_issuer(issuer)
    client = opener or build_opener(_NoRedirect())
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "BruteApply-AccessKeyRefresh/1.0"})
    try:
        with client.open(request, timeout=5) as response:
            if response.status != 200:
                raise ValueError("Access key endpoint returned a non-success status")
            raw = response.read(65537)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("Access signing keys unavailable") from exc
    if len(raw) > 65536:
        raise ValueError("Access key document exceeds size limit")
    try:
        parsed = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Access key document is invalid") from exc
    return validate_jwks(parsed)


def refresh_access_jwks() -> int:
    from .. import config, db
    from psycopg.types.json import Jsonb

    jwks = fetch_access_jwks(config.ACCESS_ISSUER)
    with db.connection() as conn:
        with conn.transaction():
            conn.execute("""INSERT INTO access_jwks_cache(id,jwks,fetched_at)
              VALUES (1,%s,now()) ON CONFLICT(id) DO UPDATE SET
              jwks=EXCLUDED.jwks,fetched_at=EXCLUDED.fetched_at""", (Jsonb(jwks),))
    return len(jwks["keys"])
