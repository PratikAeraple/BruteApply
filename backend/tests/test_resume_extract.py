from app.domain import resume_extract


class FakePage:
    def extract_text(self, extraction_mode="plain"):
        return "Technical Skills\nLinux administration\nEmail: person@example.invalid\nWork Experience\nSupport technician"


class FakeReader:
    is_encrypted = False
    pages = [FakePage(), FakePage()]


def test_resume_extraction_preserves_source_page_and_section(monkeypatch):
    monkeypatch.setattr(resume_extract, "PdfReader", lambda *_args, **_kwargs: FakeReader())
    pages, facts=resume_extract.extract_resume(b"fixture")
    assert pages == 2
    assert facts[0].text == "Linux administration"
    assert (facts[0].page,facts[0].section,facts[0].confidence)==(1,"TECHNICAL SKILLS",.99)
    assert facts[1].sensitivity == "SENSITIVE"
    assert facts[2].section == "WORK EXPERIENCE"


def test_resume_extraction_rejects_unbounded_and_encrypted_documents(monkeypatch):
    class TooMany(FakeReader):
        pages=[FakePage()] * (resume_extract.MAX_PAGES + 1)
    monkeypatch.setattr(resume_extract, "PdfReader", lambda *_args, **_kwargs: TooMany())
    try:
        resume_extract.extract_resume(b"fixture")
        assert False, "expected page limit"
    except ValueError as exc:
        assert "page count" in str(exc)

    class Encrypted(FakeReader):
        is_encrypted=True
    monkeypatch.setattr(resume_extract, "PdfReader", lambda *_args, **_kwargs: Encrypted())
    try:
        resume_extract.extract_resume(b"fixture")
        assert False, "expected encrypted PDF rejection"
    except ValueError as exc:
        assert "encrypted" in str(exc)
