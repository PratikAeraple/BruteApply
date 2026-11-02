"""Small dependency-free ATS-safe PDF and DOCX renderers for plain-text drafts."""
from __future__ import annotations

from io import BytesIO
import re
import textwrap
import zipfile
from xml.etree import ElementTree as ET


def _paragraphs(text: str) -> list[tuple[str, str]]:
    """Return (style, content) tuples; source words are kept unchanged."""
    result: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if not line:
            result.append(("blank", ""))
        elif re.fullmatch(r"[A-Z][A-Z0-9 &/·—-]{1,80}", line):
            result.append(("heading", line))
        elif line.startswith(("• ", "- ", "* ")):
            result.append(("bullet", line[2:].strip()))
        else:
            result.append(("body", line))
    return result


def render_pdf(text: str) -> bytes:
    """Render selectable, text-based Letter PDF using the built-in WinAnsi fonts."""
    lines: list[tuple[str, str]] = []
    for style, value in _paragraphs(text):
        if style == "blank":
            lines.append(("body", ""))
            continue
        width = 82 if style == "body" else 76
        chunks = textwrap.wrap(value, width=width, break_long_words=True, break_on_hyphens=False) or [""]
        lines.extend((style, ("• " if style == "bullet" else "") + chunk) for chunk in chunks)

    pages: list[list[tuple[str, str]]] = [[]]
    y = 742
    for item in lines:
        style, _ = item
        step = 17 if style == "heading" else 14
        if y < 48:
            pages.append([])
            y = 742
        pages[-1].append(item)
        y -= step

    objects: list[bytes] = []
    def add(value: bytes) -> int:
        objects.append(value)
        return len(objects)

    catalog_id = add(b"")
    pages_id = add(b"")
    regular_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    bold_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    page_ids: list[int] = []
    for page in pages:
        commands: list[str] = []
        y = 742
        for style, value in page:
            if not value:
                y -= 7
                continue
            font = "F2" if style == "heading" else "F1"
            size = 11 if style == "heading" else 9.5
            encoded = value.encode("cp1252", errors="replace").hex().upper()
            commands.append(f"BT /{font} {size} Tf 52 {y} Td <{encoded}> Tj ET")
            y -= 17 if style == "heading" else 14
        stream = "\n".join(commands).encode("ascii")
        content_id = add(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
        page_id = add(f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 {regular_id} 0 R /F2 {bold_id} 0 R >> >> /Contents {content_id} 0 R >>".encode())
        page_ids.append(page_id)
    objects[catalog_id - 1] = f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode()
    objects[pages_id - 1] = (f"<< /Type /Pages /Kids [{ ' '.join(f'{x} 0 R' for x in page_ids)}] /Count {len(page_ids)} >>").encode()
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode())
        output.extend(obj)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size {len(objects)+1} /Root {catalog_id} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(output)


def render_docx(text: str) -> bytes:
    """Build a macro-free, editable DOCX with semantic headings and paragraphs."""
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    ET.register_namespace("w", ns)
    body = ET.Element(f"{{{ns}}}body")
    for style, value in _paragraphs(text):
        paragraph = ET.SubElement(body, f"{{{ns}}}p")
        if style == "blank":
            continue
        if style == "heading":
            properties = ET.SubElement(paragraph, f"{{{ns}}}pPr")
            ET.SubElement(properties, f"{{{ns}}}pStyle", {f"{{{ns}}}val": "Heading1"})
        elif style == "bullet":
            value = "• " + value
        run = ET.SubElement(paragraph, f"{{{ns}}}r")
        ET.SubElement(run, f"{{{ns}}}t", {"{http://www.w3.org/XML/1998/namespace}space": "preserve"}).text = value
    section = ET.SubElement(body, f"{{{ns}}}sectPr")
    ET.SubElement(section, f"{{{ns}}}pgSz", {f"{{{ns}}}w": "12240", f"{{{ns}}}h": "15840"})
    ET.SubElement(section, f"{{{ns}}}pgMar", {f"{{{ns}}}top": "900", f"{{{ns}}}right": "900", f"{{{ns}}}bottom": "900", f"{{{ns}}}left": "900"})
    root = ET.Element(f"{{{ns}}}document")
    root.append(body)
    document_xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    rels = ET.Element(f"{{{rel_ns}}}Relationships")
    ET.SubElement(rels, f"{{{rel_ns}}}Relationship", {"Id":"rId1","Type":"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument","Target":"word/document.xml"})
    types_ns = "http://schemas.openxmlformats.org/package/2006/content-types"
    types = ET.Element(f"{{{types_ns}}}Types")
    ET.SubElement(types, f"{{{types_ns}}}Default", {"Extension":"rels","ContentType":"application/vnd.openxmlformats-package.relationships+xml"})
    ET.SubElement(types, f"{{{types_ns}}}Default", {"Extension":"xml","ContentType":"application/xml"})
    ET.SubElement(types, f"{{{types_ns}}}Override", {"PartName":"/word/document.xml","ContentType":"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"})
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", ET.tostring(types, encoding="utf-8", xml_declaration=True))
        archive.writestr("_rels/.rels", ET.tostring(rels, encoding="utf-8", xml_declaration=True))
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()
