"""Safely prefill structured profile fields from verified, non-sensitive resume evidence."""
from __future__ import annotations

import json
import os
from uuid import uuid4

from psycopg.types.json import Jsonb

from app import config, db
from app.storage import decrypt_document, encrypt_document, load_key_file


SECTION_FIELDS = {
    "WORK EXPERIENCE": "experience_summary",
    "EDUCATION": "education",
    "TECHNICAL SKILLS": "skills",
    "PROJECTS": "projects",
}


def main() -> int:
    if not config.DATABASE_URL:
        raise RuntimeError("database unavailable")
    db.start_pool()
    key = load_key_file(os.environ["DOCUMENT_ENCRYPTION_KEY_FILE"])
    changed = 0
    try:
        with db.connection() as conn:
            with conn.transaction():
                resume = conn.execute("""SELECT id,profile_id,sha256 FROM resume
                  WHERE canonical AND active ORDER BY imported_at DESC LIMIT 1 FOR SHARE""").fetchone()
                if not resume:
                    print("profile_prefill=SKIPPED reason=no_active_canonical_resume")
                    return 0
                profile = conn.execute("SELECT id,facts_ciphertext FROM candidate_profile WHERE id=%s FOR UPDATE",
                                       (resume["profile_id"],)).fetchone()
                if not profile:
                    print("profile_prefill=SKIPPED reason=no_private_profile")
                    return 0
                current = json.loads(decrypt_document(profile["facts_ciphertext"], key, profile["id"]))
                existing_keys = {row["fact_key"] for row in conn.execute(
                    "SELECT fact_key FROM profile_fact WHERE profile_id=%s", (profile["id"],)).fetchall()}
                facts = conn.execute("""SELECT id,section,fact_ciphertext,page_number FROM candidate_fact
                  WHERE profile_id=%s AND resume_id=%s AND source_kind='CANONICAL_RESUME'
                    AND status='RESUME_VERIFIED' AND sensitivity='NORMAL'
                  ORDER BY section,page_number,source_ordinal""", (profile["id"], resume["id"])).fetchall()
                grouped: dict[str, list[tuple[str, str, int]]] = {}
                for row in facts:
                    field = SECTION_FIELDS.get(" ".join(row["section"].upper().split()))
                    if not field:
                        continue
                    text = decrypt_document(row["fact_ciphertext"], key, row["id"]).decode("utf-8").strip()
                    if text:
                        grouped.setdefault(field, []).append((str(row["id"]), text, int(row["page_number"])))

                for field, segments in grouped.items():
                    if str(current.get(field, "")).strip() or field in existing_keys:
                        continue
                    value = "\n".join(dict.fromkeys(segment[1] for segment in segments))
                    if not value or len(value) > 5000:
                        continue
                    current[field] = value
                    fact_id = uuid4()
                    provenance = {"source": "canonical-resume", "resume_id": str(resume["id"]),
                                  "resume_sha256": resume["sha256"],
                                  "source_fact_ids": [segment[0] for segment in segments],
                                  "pages": sorted({segment[2] for segment in segments}),
                                  "review_status": "RESUME_VERIFIED", "owner_approved": False}
                    conn.execute("""INSERT INTO profile_fact(id,profile_id,fact_key,value_ciphertext,verified,
                      source,sensitivity,provenance) VALUES (%s,%s,%s,%s,true,'canonical-resume; source verified',
                      'NORMAL',%s)""",
                      (fact_id, profile["id"], field, encrypt_document(value.encode(), key, fact_id), Jsonb(provenance)))
                    existing_keys.add(field)
                    changed += 1
                conn.execute("UPDATE candidate_profile SET facts_ciphertext=%s,updated_at=now() WHERE id=%s",
                             (encrypt_document(json.dumps(current, sort_keys=True, ensure_ascii=False).encode(), key,
                                               profile["id"]), profile["id"]))
                if changed:
                    conn.execute("""INSERT INTO audit_event(id,actor,action,object_type,object_id,request_id,result,metadata)
                      VALUES (%s,'profile-prefill','PROFILE_PREFILLED_FROM_CANONICAL_RESUME','candidate_profile',%s,%s,
                      'RESUME_VERIFIED',%s)""", (uuid4(), profile["id"], uuid4(), Jsonb({"field_count": changed})))
        print(f"profile_prefill=COMPLETE fields_added={changed} source=canonical_resume_verified_non_sensitive")
        return 0
    finally:
        db.close_pool()


if __name__ == "__main__":
    raise SystemExit(main())
