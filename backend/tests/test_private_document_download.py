from __future__ import annotations

import asyncio
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

from pypdf import PdfWriter

from app import config, main
from app.auth import Principal
from app.storage import encrypt_document


class _Cursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _Connection:
    def __init__(self, row):
        self.row = row

    def execute(self, query, params):
        assert "document_type" in query
        return _Cursor(self.row)


def _pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _docx() -> bytes:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        archive.writestr(
            "word/document.xml",
            '<document xmlns="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>',
        )
    return output.getvalue()


def test_private_document_download_returns_verified_pdf(monkeypatch, tmp_path: Path):
    import hashlib

    identity = uuid4()
    key = b"k" * 32
    data = _pdf()
    relative = f"{identity.hex[:2]}/{identity.hex}.pdf.enc"
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(encrypt_document(data, key, identity))
    row = {"document_type": "TAILORED_RESUME", "sha256": hashlib.sha256(data).hexdigest(),
           "mime_type": "application/pdf", "size_bytes": len(data), "storage_key": relative}

    @contextmanager
    def connection():
        yield _Connection(row)

    monkeypatch.setattr(config, "DEMO_MODE", False)
    monkeypatch.setattr(config, "DOCUMENT_ROOT", tmp_path)
    monkeypatch.setattr(main, "_document_key", lambda: key)
    monkeypatch.setattr(main.db, "connection", connection)
    response = asyncio.run(main.download_private_document(identity, Principal("owner@example.test", "test")))

    assert response.body == data
    assert response.media_type == "application/pdf"
    assert response.headers["content-disposition"].endswith(".pdf")
    assert response.headers["cache-control"] == "private, no-store"


def test_private_document_download_returns_verified_docx(monkeypatch, tmp_path: Path):
    import hashlib

    identity = uuid4()
    key = b"d" * 32
    data = _docx()
    relative = f"{identity.hex[:2]}/{identity.hex}.docx.enc"
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(encrypt_document(data, key, identity))
    row = {"document_type": "COVER_LETTER", "sha256": hashlib.sha256(data).hexdigest(),
           "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
           "size_bytes": len(data), "storage_key": relative}

    @contextmanager
    def connection():
        yield _Connection(row)

    monkeypatch.setattr(config, "DEMO_MODE", False)
    monkeypatch.setattr(config, "DOCUMENT_ROOT", tmp_path)
    monkeypatch.setattr(main, "_document_key", lambda: key)
    monkeypatch.setattr(main.db, "connection", connection)
    response = asyncio.run(main.download_private_document(identity, Principal("owner@example.test", "test")))

    assert response.body == data
    assert response.media_type == row["mime_type"]
    assert response.headers["content-disposition"].endswith(".docx")
    assert response.headers["x-content-type-options"] == "nosniff"
