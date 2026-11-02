from io import BytesIO
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from pypdf import PdfReader

from app.domain.document_export import render_docx, render_pdf


SOURCE = "PRATIK AERAPLE\n\nPROFESSIONAL SUMMARY\nIT support and systems troubleshooting.\n\nTECHNICAL SKILLS\n• Linux, Windows, and TCP/IP\n"


def test_pdf_is_readable_selectable_and_contains_only_source_text():
    data = render_pdf(SOURCE)
    reader = PdfReader(BytesIO(data), strict=True)
    assert len(reader.pages) == 1
    output = reader.pages[0].extract_text()
    assert "PRATIK AERAPLE" in output
    assert "PROFESSIONAL SUMMARY" in output
    assert "Linux, Windows, and TCP/IP" in output


def test_docx_is_valid_editable_office_package():
    data = render_docx(SOURCE)
    with ZipFile(BytesIO(data)) as archive:
        assert archive.testzip() is None
        assert "[Content_Types].xml" in archive.namelist()
        root = ET.fromstring(archive.read("word/document.xml"))
    text = " ".join(node.text or "" for node in root.iter() if node.tag.endswith("}t"))
    assert "PRATIK AERAPLE" in text
    assert "PROFESSIONAL SUMMARY" in text
    assert "Linux, Windows, and TCP/IP" in text
