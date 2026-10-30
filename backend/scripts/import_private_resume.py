"""Idempotently import owner-encrypted resume evidence into private production storage.

The input directory must contain only encrypted owner documents and manifests.
PDF and fact payloads are decrypted in memory, validated against their recorded
checksums/provenance, and encrypted again under fresh database/document IDs.
Nothing from the candidate document is printed or written as plaintext.
"""
from __future__ import annotations

import argparse
from collections import defaultdict, deque
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid
from io import BytesIO

import psycopg
from psycopg.types.json import Jsonb
from psycopg.rows import dict_row
from pypdf import PdfReader

from app.domain.fact_review import FactStatus, ResumeFact, initial_status, validate_resume_fact
from app.domain.jobs import normalize_text
from app.domain.resume_extract import extract_resume
from app.storage import (decrypt_document, encrypt_document, load_key_file, safe_display_name,
                         store_private, validate_pdf)


def _read_regular(path: Path, *, maximum: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeError("bootstrap input is not a regular file")
        mode = stat.S_IMODE(info.st_mode)
        if mode not in {0o600, 0o640} or (info.st_uid != os.getuid() and info.st_gid not in os.getgroups()):
            raise RuntimeError("bootstrap input permissions are invalid")
        if info.st_size > maximum:
            raise RuntimeError("bootstrap input exceeds its size limit")
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise RuntimeError("bootstrap input exceeds its size limit")
    return data


def _manifest(root: Path, name: str) -> dict:
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError("invalid manifest path")
    path = root / relative
    value = json.loads(_read_regular(path, maximum=32_768))
    if not isinstance(value, dict):
        raise RuntimeError("invalid resume manifest")
    return value


def _safe_source(root: Path, name: str) -> Path:
    rel = Path(name)
    if rel.is_absolute() or ".." in rel.parts:
        raise RuntimeError("invalid encrypted source path")
    path = root / rel
    _read_regular(path, maximum=20 * 1024 * 1024)
    return path


def _decode_resume(manifest: dict, root: Path, key: bytes, *, current: bool):
    expected_flag = current
    if manifest.get("canonical") is not expected_flag or manifest.get("active") is not expected_flag:
        raise RuntimeError("resume active/canonical state does not match the import role")
    identity_key = "id" if current else "document_id"
    document_id = uuid.UUID(str(manifest[identity_key]))
    encrypted = _read_regular(_safe_source(root, manifest["storage_key"]), maximum=20 * 1024 * 1024)
    data = decrypt_document(encrypted, key, document_id)
    digest = hashlib.sha256(data).hexdigest()
    if digest != manifest.get("sha256") or len(data) != int(manifest.get("size_bytes", -1)):
        raise RuntimeError("resume checksum or size does not match its manifest")
    filename = safe_display_name(str(manifest.get("filename", "resume.pdf")))
    validate_pdf(filename, str(manifest.get("mime_type", "")), data)
    page_count, facts = extract_resume(data)
    recorded_pages = manifest.get("page_count")
    if current and recorded_pages != page_count:
        raise RuntimeError("canonical resume page count does not match its manifest")
    if current and (page_count != 2 or len(data) != 131_942 or not digest.startswith("042f5b8b655f60a5")):
        raise RuntimeError("canonical resume does not match the owner-verified current document")
    if not current and not str(manifest.get("status", "")).startswith("SUPERSEDED"):
        raise RuntimeError("previous resume is not marked superseded")
    return data, digest, page_count, facts, filename


def _verified_source_facts(root: Path, key: bytes, canonical_manifest: dict,
                           extracted: list, canonical_sha: str, *, expected_count: int = 104) -> list:
    fact_manifest = _manifest(root, "candidate-facts.manifest.json")
    if fact_manifest.get("encrypted") is not True or fact_manifest.get("fact_count") != expected_count:
        raise RuntimeError("prepared candidate fact manifest is incomplete")
    sha_prefix = str(fact_manifest.get("source_sha256_prefix", ""))
    if not re.fullmatch(r"[0-9a-f]{16,64}", sha_prefix) or not canonical_sha.startswith(sha_prefix):
        raise RuntimeError("candidate facts refer to a different canonical resume")
    blob = _read_regular(_safe_source(root, fact_manifest["storage_key"]), maximum=2 * 1024 * 1024)
    raw = decrypt_document(blob, key, uuid.UUID(str(fact_manifest["id"])))
    payload = json.loads(raw)
    source_facts = payload if isinstance(payload, list) else payload.get("facts")
    if not isinstance(source_facts, list) or len(source_facts) != expected_count or len(extracted) != expected_count:
        raise RuntimeError("prepared candidate facts do not match the current extraction")

    index = defaultdict(deque)
    for fact in extracted:
        index[(fact.page, fact.section, normalize_text(fact.text))].append(fact)
    verified = []
    for ordinal, source in enumerate(source_facts):
        try:
            key_tuple = (int(source["page"]), str(source["section"]).upper(),
                         normalize_text(str(source["text"])))
            extracted_fact = index[key_tuple].popleft()
        except (KeyError, IndexError, TypeError, ValueError):
            raise RuntimeError("candidate fact provenance failed exact source validation") from None
        checked = validate_resume_fact(ResumeFact(extracted_fact.text, extracted_fact.page,
                                                    extracted_fact.section, extracted_fact.confidence,
                                                    extracted_fact.sensitivity), 2)
        verified.append((ordinal, checked, initial_status(canonical_resume=True,
                                                           confidence=checked.confidence,
                                                           sensitivity=checked.sensitivity)))
    if any(queue for queue in index.values()):
        raise RuntimeError("canonical resume contains unaccounted extracted facts")
    return verified


def _read_db_url(path: Path) -> str:
    raw = _read_regular(path, maximum=4096).decode("utf-8").strip()
    if not raw.startswith("postgresql://"):
        raise RuntimeError("production database URL is invalid")
    return raw


def import_resume(source: Path, database_url_file: Path, key_file: Path, document_root: Path) -> dict:
    if source.is_symlink() or not source.is_dir():
        raise RuntimeError("private bootstrap directory is unavailable")
    source_info = source.stat()
    if (stat.S_IMODE(source_info.st_mode) != 0o750
            or (source_info.st_uid != os.getuid() and source_info.st_gid not in os.getgroups())):
        raise RuntimeError("private bootstrap directory permissions are invalid")
    key = load_key_file(str(key_file))
    canonical_manifest = _manifest(source, "canonical-resume-v2.manifest.json")
    current, canonical_sha, page_count, extracted, display_name = _decode_resume(
        canonical_manifest, source, key, current=True)
    previous_manifest = _manifest(source, "resume-v1.manifest.json")
    previous, previous_sha, previous_pages, _, previous_name = _decode_resume(
        previous_manifest, source, key, current=False)
    facts = _verified_source_facts(source, key, canonical_manifest, extracted, canonical_sha)
    db_url = _read_db_url(database_url_file)
    new_files: list[Path] = []
    inserted_facts = 0
    try:
      with psycopg.connect(db_url, row_factory=dict_row) as conn:
        with conn.transaction():
            profile = conn.execute("SELECT id FROM candidate_profile LIMIT 1 FOR UPDATE").fetchone()
            if profile:
                profile_id = profile["id"]
            else:
                profile_id = uuid.uuid4()
                conn.execute("INSERT INTO candidate_profile(id,facts_ciphertext) VALUES (%s,%s)",
                             (profile_id, encrypt_document(b"{}", key, profile_id)))

            def register(version: str, data: bytes, digest: str, pages: int, filename: str,
                         manifest: dict, *, is_current: bool):
                prior = conn.execute("SELECT id,canonical,active FROM resume WHERE profile_id=%s AND sha256=%s",
                                     (profile_id, digest)).fetchone()
                if prior:
                    if bool(prior["canonical"]) != is_current or bool(prior["active"]) != is_current:
                        raise RuntimeError("existing resume record has conflicting active/canonical state")
                    return prior["id"]
                collision = conn.execute("SELECT 1 FROM resume WHERE profile_id=%s AND version=%s",
                                         (profile_id, version)).fetchone()
                if collision:
                    raise RuntimeError("resume version is already assigned to different content")
                document_id, storage_key, stored_digest = store_private(
                    data, filename, document_root, key)
                new_files.append(document_root / storage_key)
                if stored_digest != digest:
                    raise RuntimeError("private resume re-encryption checksum mismatch")
                provenance = {"import":"owner-encrypted-bootstrap", "source_revision":manifest.get("source_revision"),
                              "source_sha256":digest, "status":"CANONICAL_ACTIVE" if is_current else "SUPERSEDED",
                              "source_imported_at_utc":manifest.get("imported_at_utc"),
                              "production_imported_at_utc":datetime.now(timezone.utc).isoformat(),
                              "encrypted_at_import":True}
                imported_at = datetime.now(timezone.utc)
                conn.execute("""INSERT INTO resume(id,profile_id,version,label,category,sha256,mime_type,
                  size_bytes,storage_key,canonical,active,imported_at,page_count,provenance)
                  VALUES (%s,%s,%s,%s,'GENERAL',%s,'application/pdf',%s,%s,%s,%s,%s,%s,%s)""",
                  (document_id,profile_id,version,
                   "Canonical resume" if is_current else "Superseded previous resume",digest,len(data),
                   storage_key,is_current,is_current,imported_at,pages,Jsonb(provenance)))
                return document_id

            canonical_id = register("resume-v2", current, canonical_sha, page_count,
                                     display_name, canonical_manifest, is_current=True)
            register("resume-v1", previous, previous_sha, previous_pages,
                     previous_name, previous_manifest, is_current=False)

            for ordinal, fact, status in facts:
                fact_id = uuid.uuid4()
                result = conn.execute("""INSERT INTO candidate_fact(id,profile_id,resume_id,fact_ciphertext,
                  source_sha256,source_kind,source_ordinal,page_number,section,confidence,sensitivity,status,
                  provenance)
                  VALUES (%s,%s,%s,%s,%s,'CANONICAL_RESUME',%s,%s,%s,%s,%s,%s,%s)
                  ON CONFLICT (resume_id,source_sha256,source_ordinal)
                  WHERE source_kind='CANONICAL_RESUME' DO NOTHING""",
                  (fact_id,profile_id,canonical_id,encrypt_document(fact.text.encode("utf-8"),key,fact_id),
                   canonical_sha,ordinal,fact.page,fact.section,fact.confidence,fact.sensitivity,status.value,
                   Jsonb({"source":"canonical-resume","source_sha256":canonical_sha,
                          "source_revision":canonical_manifest.get("source_revision"),"page":fact.page,
                          "section":fact.section,"ordinal":ordinal,
                          "meaning":"resume evidence only; not owner approval"})))
                inserted_facts += result.rowcount

            conn.execute("""UPDATE agent_configuration SET enabled=false,paused=true,drain=false,
              emergency_stopped=true,updated_by='private-resume-bootstrap',updated_at=now()
              WHERE agent='submission'""")
            conn.execute("""INSERT INTO audit_event(id,actor,action,object_type,object_id,request_id,result,metadata)
              VALUES (%s,'private-bootstrap','CANONICAL_RESUME_IMPORTED','resume',%s,%s,'VERIFIED',%s)""",
              (uuid.uuid4(),canonical_id,uuid.uuid4(),Jsonb({"page_count":page_count,
                "size_bytes":len(current),"sha256_prefix":canonical_sha[:16],"fact_count":len(facts),
                "inserted_facts":inserted_facts,"previous_resume":"SUPERSEDED"})))
      return {"resume_versions":2,"facts":len(facts),"inserted_facts":inserted_facts,
              "resume_verified":sum(status == FactStatus.RESUME_VERIFIED for _,_,status in facts),
              "needs_owner_review":sum(status == FactStatus.NEEDS_REVIEW for _,_,status in facts)}
    except Exception:
      for path in new_files:
        try:
          path.unlink()
          path.parent.rmdir()
        except OSError:
          pass
      raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--database-url-file", type=Path, default=Path("/run/secrets/database_url"))
    parser.add_argument("--key-file", type=Path, default=Path("/run/secrets/document_encryption_key"))
    parser.add_argument("--document-root", type=Path, default=Path("/var/lib/bruteapply/documents"))
    args = parser.parse_args()
    try:
        result = import_resume(args.source_dir,args.database_url_file,args.key_file,args.document_root)
        print("Canonical resume import verified; personal data remained encrypted and was not displayed.")
        print("versions={resume_versions} facts={facts} inserted={inserted_facts} resume_verified={resume_verified} needs_owner_review={needs_owner_review}".format(**result))
        return 0
    except Exception as exc:
        # Error categories only; parser/provider exceptions can include source text.
        print("Canonical resume import failed; details withheld ("+type(exc).__name__+")",file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
