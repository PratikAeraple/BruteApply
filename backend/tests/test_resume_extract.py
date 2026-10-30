from app.domain import resume_extract
from app.storage import encrypt_document
from app.domain.fact_review import FactStatus
import json
import sys
import uuid


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


def test_private_resume_bootstrap_verifies_fact_provenance_without_approving_sensitive_facts(tmp_path):
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "scripts"))
    from import_private_resume import _verified_source_facts

    key=b"k"*32
    source_id=uuid.uuid4()
    payload={"facts":[
        {"page":1,"section":"PROFILE","text":"Linux support technician","source":"canonical"},
        {"page":2,"section":"CONTACT","text":"Email: person@example.invalid","source":"canonical"},
    ]}
    encrypted=encrypt_document(json.dumps(payload).encode(),key,source_id)
    encrypted_path=tmp_path/"facts.enc"
    encrypted_path.write_bytes(encrypted); encrypted_path.chmod(0o640)
    manifest_path=tmp_path/"candidate-facts.manifest.json"
    manifest_path.write_text(json.dumps({
        "encrypted":True,"fact_count":2,"source_sha256_prefix":"a"*16,
        "id":str(source_id),"storage_key":"facts.enc"})); manifest_path.chmod(0o640)
    extracted=[
        resume_extract.ExtractedFact(0,"Linux support technician",1,"PROFILE",.99,"NORMAL"),
        resume_extract.ExtractedFact(1,"Email: person@example.invalid",2,"CONTACT",.99,"SENSITIVE"),
    ]
    facts=_verified_source_facts(tmp_path,key,{},extracted,"a"*64,expected_count=2)
    assert len(facts)==2
    assert facts[0][2] == FactStatus.RESUME_VERIFIED
    assert facts[1][2] == FactStatus.NEEDS_REVIEW


def test_private_resume_bootstrap_rejects_mismatched_page_or_text_provenance(tmp_path):
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "scripts"))
    from import_private_resume import _verified_source_facts

    key=b"k"*32
    source_id=uuid.uuid4()
    payload={"facts":[{"page":2,"section":"PROFILE","text":"Unsupported source text"}]}
    encrypted_path=tmp_path/"facts.enc"
    encrypted_path.write_bytes(encrypt_document(json.dumps(payload).encode(),key,source_id)); encrypted_path.chmod(0o640)
    manifest_path=tmp_path/"candidate-facts.manifest.json"
    manifest_path.write_text(json.dumps({
        "encrypted":True,"fact_count":1,"source_sha256_prefix":"a"*16,
        "id":str(source_id),"storage_key":"facts.enc"})); manifest_path.chmod(0o640)
    extracted=[resume_extract.ExtractedFact(0,"Verified source line",1,"PROFILE",.99,"NORMAL")]
    try:
        _verified_source_facts(tmp_path,key,{},extracted,"a"*64,expected_count=1)
        assert False, "expected source mismatch rejection"
    except RuntimeError as exc:
        assert "provenance" in str(exc)
