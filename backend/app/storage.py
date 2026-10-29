from __future__ import annotations

import hashlib
import base64
import os
from pathlib import Path
import re
import secrets
import stat
import uuid


MAX_UPLOAD_BYTES = 15 * 1024 * 1024
PDF_MAGIC = b"%PDF-"


def validate_pdf(filename: str, content_type: str, data: bytes) -> None:
    suffix = Path(filename).suffix.casefold()
    if suffix != ".pdf" or content_type != "application/pdf":
        raise ValueError("only PDF resumes are accepted")
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("document is empty or exceeds the 15 MiB limit")
    if not data.startswith(PDF_MAGIC) or b"%%EOF" not in data[-2048:]:
        raise ValueError("file signature is not a complete PDF")


def safe_display_name(filename: str) -> str:
    name = Path(filename.replace("\\", "/")).name
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")[:120]
    if not name:
        raise ValueError("invalid document filename")
    return name


def _key_from_file(path_value: str) -> bytes:
    path = Path(path_value)
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("document encryption key unavailable")
    info = path.stat()
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise RuntimeError("document encryption key permissions invalid")
    try:
        raw = base64.urlsafe_b64decode(path.read_text(encoding="ascii").strip())
    except Exception:
        raise RuntimeError("document encryption key format invalid") from None
    if len(raw) != 32:
        raise RuntimeError("document encryption key must encode 32 bytes")
    return raw


def load_key_file(path_value: str) -> bytes:
    return _key_from_file(path_value)


def encrypt_document(data: bytes, key: bytes, doc_id: uuid.UUID) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = secrets.token_bytes(12)
    return b"BA1" + nonce + AESGCM(key).encrypt(nonce, data, doc_id.bytes)


def decrypt_document(blob: bytes, key: bytes, doc_id: uuid.UUID) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if len(blob) < 31 or blob[:3] != b"BA1":
        raise ValueError("unsupported encrypted document format")
    return AESGCM(key).decrypt(blob[3:15], blob[15:], doc_id.bytes)


def store_private(data: bytes, display_name: str, root: Path, key: bytes) -> tuple[uuid.UUID, str, str]:
    doc_id = uuid.uuid4()
    if root.is_symlink():
        raise RuntimeError("document storage root may not be a symlink")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    # Never derive storage paths from a user-supplied name.
    rel = f"{doc_id.hex[:2]}/{doc_id.hex}.pdf.enc"
    dest = root / rel
    dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    encrypted = encrypt_document(data, key, doc_id)
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(encrypted)
        stream.flush()
        os.fsync(stream.fileno())
    return doc_id, rel, hashlib.sha256(data).hexdigest()
