from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import sys

import psycopg


def read_db_url() -> str:
    path_value = os.getenv("DATABASE_URL_FILE")
    if not path_value:
        if os.getenv("APP_ENV") == "production":
            raise RuntimeError("production DATABASE_URL_FILE is required")
        return os.environ["DATABASE_URL"]
    path = Path(path_value)
    if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o037:
        raise RuntimeError("database secret file permissions are invalid")
    return path.read_text(encoding="utf-8").strip()


def apply_migrations(directory: Path, conn: psycopg.Connection) -> list[str]:
    conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
      version TEXT PRIMARY KEY, checksum CHAR(64) NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    conn.commit()
    applied = []
    for path in sorted(directory.glob("[0-9]*.sql")):
        sql = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(sql.encode()).hexdigest()
        prior = conn.execute("SELECT checksum FROM schema_migrations WHERE version=%s", (path.name,)).fetchone()
        if prior:
            if prior[0].strip() != checksum:
                raise RuntimeError(f"applied migration checksum mismatch: {path.name}")
            continue
        with conn.transaction():
            conn.execute(sql, prepare=False)
            conn.execute("INSERT INTO schema_migrations(version,checksum) VALUES (%s,%s)", (path.name, checksum))
        applied.append(path.name)
    return applied


if __name__ == "__main__":
    try:
        with psycopg.connect(read_db_url()) as connection:
            changed = apply_migrations(Path(__file__).resolve().parents[1] / "migrations", connection)
        print("Migrations applied: " + (", ".join(changed) if changed else "already current"))
    except Exception:
        print("Migration failed; database details withheld", file=sys.stderr)
        sys.exit(1)
