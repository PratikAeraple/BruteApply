from __future__ import annotations

import os
import re
import stat
from pathlib import Path


class ConfigurationError(RuntimeError):
    pass


def _secret(name: str) -> str | None:
    path_value = os.environ.get(f"{name}_FILE")
    if not path_value:
        return os.environ.get(name)
    path = Path(path_value)
    if path.is_symlink() or not path.is_file():
        raise ConfigurationError(f"invalid secret file for {name}")
    info = path.stat()
    mode = stat.S_IMODE(info.st_mode)
    if mode not in {0o600, 0o640} or (info.st_uid != os.getuid() and info.st_gid not in os.getgroups()):
        raise ConfigurationError(f"secret file for {name} is too broadly readable")
    return path.read_text(encoding="utf-8").strip()


APP_ENV = os.getenv("APP_ENV", "development")
DEMO_MODE = os.getenv("DEMO_MODE", "true").lower() == "true"
OWNER_EMAIL = os.getenv("OWNER_EMAIL", "owner@example.invalid").strip().casefold()
ACCESS_ISSUER = os.getenv("CF_ACCESS_ISSUER", "").rstrip("/")
ACCESS_AUD = os.getenv("CF_ACCESS_AUD", "")
DATABASE_URL = _secret("DATABASE_URL")
REDIS_URL = _secret("REDIS_URL") or "redis://redis:6379/0"
DOCUMENT_ROOT = Path(os.getenv("DOCUMENT_ROOT", "/var/lib/bruteapply/documents"))
DOCUMENT_KEY = _secret("DOCUMENT_ENCRYPTION_KEY")
TIMEZONE = os.getenv("TIMEZONE", "America/Toronto")
AUTONOMOUS_SUBMISSION_ENABLED = os.getenv("AUTONOMOUS_SUBMISSION_ENABLED", "false").lower() == "true"
SOURCE_REVISION = os.getenv("SOURCE_REVISION", "unknown")


def validate_production_config() -> None:
    if DEMO_MODE:
        return
    if APP_ENV != "production":
        raise ConfigurationError("production requires APP_ENV=production")
    if not OWNER_EMAIL or OWNER_EMAIL.endswith(".invalid"):
        raise ConfigurationError("an explicit owner identity is required")
    if not ACCESS_ISSUER or not ACCESS_AUD:
        raise ConfigurationError("exact Cloudflare Access issuer and audience are required")
    if not re.fullmatch(r"[0-9a-f]{40}", SOURCE_REVISION):
        raise ConfigurationError("a full immutable BruteApply source revision is required")
    if not DATABASE_URL or not DATABASE_URL.startswith("postgresql"):
        raise ConfigurationError("a PostgreSQL secret file is required")
    if not DOCUMENT_KEY:
        raise ConfigurationError("a document encryption key file is required")
