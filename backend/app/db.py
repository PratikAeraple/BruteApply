from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row

_pool: ConnectionPool | None = None


def database_url() -> str:
    path = os.getenv("DATABASE_URL_FILE")
    if path:
        p = Path(path)
        if p.is_symlink() or not p.is_file() or (p.stat().st_mode & 0o037):
            raise RuntimeError("database secret file permissions are invalid")
        return p.read_text(encoding="utf-8").strip()
    if os.getenv("APP_ENV") == "production":
        raise RuntimeError("production DATABASE_URL_FILE is required")
    return os.getenv("DATABASE_URL", "")


def start_pool() -> None:
    global _pool
    url = database_url()
    if not url:
        return
    _pool = ConnectionPool(url, kwargs={"row_factory": dict_row}, min_size=1, max_size=8, open=True, timeout=5)


def close_pool() -> None:
    global _pool
    if _pool:
        _pool.close()
        _pool = None


@contextmanager
def connection():
    if _pool is None:
        raise RuntimeError("database is unavailable")
    with _pool.connection() as conn:
        yield conn


def ready() -> bool:
    try:
        with connection() as conn:
            row = conn.execute("SELECT 1").fetchone()
            return bool(row and next(iter(row.values())) == 1)
    except Exception:
        return False
