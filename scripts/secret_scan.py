#!/usr/bin/env python3
"""Small fail-closed scan for accidental credential/private-data files in source."""
from __future__ import annotations

import re
import sys
from pathlib import Path

PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{24,}\b"),
    re.compile(r"\bre_[A-Za-z0-9_-]{24,}\b"),
    re.compile(r"\b[A-Za-z0-9_-]{40,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"-----BEGIN PGP " + r"PRIVATE KEY BLOCK-----"),
]
FORBIDDEN_SUFFIXES = {".pdf", ".doc", ".docx", ".sqlite", ".sqlite3", ".db", ".dump", ".backup"}
SKIP = {".git", "node_modules", ".next", ".venv", "venv", "coverage", "__pycache__"}


def scan(root: Path) -> list[str]:
    findings: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or any(part in SKIP for part in path.relative_to(root).parts):
            continue
        rel = path.relative_to(root)
        if path.suffix.casefold() in FORBIDDEN_SUFFIXES:
            findings.append(f"private-data file type: {rel.as_posix()}")
            continue
        if path.name == ".env" or (path.name.startswith(".env.") and path.name != ".env.example"):
            findings.append(f"runtime environment file: {rel.as_posix()}")
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if any(pattern.search(content) for pattern in PATTERNS):
            findings.append(f"credential-like value: {rel.as_posix()}")
    return findings


if __name__ == "__main__":
    root = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]).resolve()
    results = scan(root)
    for result in results:
        print(result)
    print(f"secret scan: {len(results)} finding(s)")
    raise SystemExit(bool(results))
