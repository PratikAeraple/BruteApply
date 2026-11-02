"""One-shot refresh; run only from the egress-enabled worker service."""
from __future__ import annotations

from app import db
from app.integrations.access_jwks import refresh_access_jwks


if __name__ == "__main__":
    try:
        db.start_pool()
        count=refresh_access_jwks()
        print(f"Cloudflare Access public signing keys refreshed: {count}")
    finally:
        db.close_pool()
