from __future__ import annotations

import hashlib

from fastapi import HTTPException

from . import db
from .auth import Principal


def enforce_owner_limit(principal: Principal, route_class: str, maximum: int, window_seconds: int = 60) -> None:
    """Atomic DB-backed fixed-window limit keyed by the verified Access subject."""
    if not route_class.isascii() or not route_class.replace("-", "").isalnum() or len(route_class) > 48:
        raise ValueError("invalid rate-limit class")
    if maximum < 1 or window_seconds < 1:
        raise ValueError("invalid rate-limit policy")
    actor_hash = hashlib.sha256(principal.subject.encode("utf-8")).hexdigest()
    with db.connection() as conn:
        with conn.transaction():
            row = conn.execute("""
              WITH bucket AS (
                SELECT to_timestamp(floor(extract(epoch from now()) / %s) * %s) AS start_at
              )
              INSERT INTO api_rate_limit(actor_hash,route_class,window_start,request_count)
              SELECT %s,%s,start_at,1 FROM bucket
              ON CONFLICT(actor_hash,route_class) DO UPDATE SET
                request_count=CASE WHEN api_rate_limit.window_start < EXCLUDED.window_start
                                   THEN 1 ELSE api_rate_limit.request_count + 1 END,
                window_start=GREATEST(api_rate_limit.window_start,EXCLUDED.window_start)
              WHERE api_rate_limit.window_start < EXCLUDED.window_start
                 OR (api_rate_limit.window_start = EXCLUDED.window_start
                     AND api_rate_limit.request_count < %s)
              RETURNING request_count
            """, (window_seconds,window_seconds,actor_hash,route_class,maximum)).fetchone()
    if row is None:
        raise HTTPException(429,"request limit reached; retry after the current window")
