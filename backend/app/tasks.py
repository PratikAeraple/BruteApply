"""Durable, policy-gated BruteApply worker tasks.

The worker deliberately implements public ATS discovery only. Integrations that
need owner OAuth, private credentials, or a permitted submission handler remain
fail-closed instead of being simulated as production work.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import logging
import re
from zoneinfo import ZoneInfo
from uuid import uuid4

from celery import Celery
from celery.schedules import crontab
from psycopg.types.json import Jsonb

from . import config, db
from .domain.jobs import display_city, normalize_job, target_job_scope
from .domain.matching import SKILL_ALIASES, score_job
from .providers.public_ats import PublicATSProvider, PublicBoard

logger = logging.getLogger("bruteapply.worker")

celery_app = Celery("bruteapply", broker=config.REDIS_URL, backend=config.REDIS_URL)
celery_app.conf.update(
    task_serializer="json", result_serializer="json", accept_content=["json"],
    timezone="America/Toronto", enable_utc=True, task_acks_late=True,
    task_acks_on_failure_or_timeout=False, task_reject_on_worker_lost=True,
    task_ignore_result=True, worker_prefetch_multiplier=1,
    broker_transport_options={"visibility_timeout":3600}, result_expires=3600,
    task_default_delivery_mode="persistent",
    task_routes={"bruteapply.sync_google_sheet":{"queue":"google-sheets"}},
    task_default_retry_delay=60, task_max_retries=5,
    beat_schedule={
        "schedule-permitted-public-discovery-morning": {
            "task": "bruteapply.discover_enabled_sources", "schedule": crontab(minute=15, hour=7),
        },
        "schedule-permitted-public-discovery-midday": {
            "task": "bruteapply.discover_enabled_sources", "schedule": crontab(minute=15, hour=12),
        },
        "schedule-permitted-public-discovery-evening": {
            "task": "bruteapply.discover_enabled_sources", "schedule": crontab(minute=15, hour=17),
        },
        "schedule-google-sheets-sync-four-hourly": {
            "task": "bruteapply.sync_google_sheet", "schedule": crontab(minute=0, hour="*/4"),
        },
        "schedule-profile-job-rematching-four-hourly": {
            "task": "bruteapply.match_enabled_jobs", "schedule": crontab(minute=20, hour="*/4"),
        },
        "schedule-private-document-export-four-hourly": {
            "task": "bruteapply.prepare_documents", "schedule": crontab(minute=35, hour="*/4"),
        },
        "refresh-cloudflare-access-signing-keys": {
         "task": "bruteapply.refresh_access_jwks", "schedule": crontab(minute=7, hour="*/6"),
        },
    },
)


@celery_app.on_after_configure.connect
def _configure_worker(sender, **kwargs):
    # Celery's beat process does not need a DB connection pool. Worker startup
    # opens one lazily in task bodies so module import remains safe for CI.
    return None


def _ensure_db() -> None:
    if not db.ready():
        db.start_pool()
    if not db.ready():
        raise RuntimeError("database unavailable")


@celery_app.task(name="bruteapply.refresh_access_jwks", bind=True, max_retries=3)
def refresh_access_jwks_task(self):
    """Refresh only public Access signing keys from the egress-enabled worker."""
    _ensure_db()
    try:
        from .integrations.access_jwks import refresh_access_jwks
        key_count=refresh_access_jwks()
    except Exception:
        logger.warning("Cloudflare Access signing-key refresh failed")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=RuntimeError("Access signing-key refresh failed"),
                             countdown=min(60*(2**self.request.retries),1800))
        raise RuntimeError("Access signing-key refresh failed") from None
    logger.info("Cloudflare Access signing-key cache refreshed key_count=%d",key_count)
    return {"key_count":key_count}


def _claim_task(conn, key: str, name: str) -> bool:
    row = conn.execute("""INSERT INTO worker_job(id,idempotency_key,task_name,state,attempts,started_at)
      VALUES (%s,%s,%s,'RUNNING',1,now()) ON CONFLICT(idempotency_key) DO UPDATE
      SET state='RUNNING',attempts=worker_job.attempts+1,started_at=now(),error_category=NULL
      WHERE (worker_job.state IN ('FAILED','RETRY') OR
        (worker_job.state='RUNNING' AND worker_job.started_at<now()-interval '1 hour'))
        AND worker_job.attempts<5
      RETURNING id""", (uuid4(), key, name)).fetchone()
    return row is not None


def _finish_task(conn, key: str, state: str = "SUCCEEDED", error: str | None = None,
                 payload: dict | None = None) -> None:
    conn.execute("""UPDATE worker_job SET state=CASE WHEN %s='FAILED' AND attempts>=5 THEN 'DEAD_LETTER' ELSE %s END,
      completed_at=now(),error_category=%s,payload=%s
      WHERE idempotency_key=%s""", (state, state, error, Jsonb(payload or {}), key))


def _insert_job(conn, source_id, board_name: str, raw: dict) -> tuple[bool, object | None]:
    scope = target_job_scope(str(raw.get("title", "")), str(raw.get("location", "")), raw.get("work_mode"))
    if scope is None:
        return False, None
    province=scope["province"] if scope["province"]!="Remote Canada" else None
    job = normalize_job(raw)
    # Serialize identical fingerprints across source scans before duplicate
    # lookup; the lock is transaction-scoped and does not block other postings.
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (job.fingerprint,))
    prior = conn.execute("""SELECT id::text FROM job WHERE canonical_url=%s OR posting_fingerprint=%s
      OR (%s::text IS NOT NULL AND source_id=%s AND external_id=%s) LIMIT 1""",
      (job.canonical_url, job.fingerprint, job.external_id, source_id, job.external_id)).fetchone()
    if prior:
        return False, prior["id"]
    identity=uuid4()
    desc_hash=hashlib.sha256(job.description.encode("utf-8")).hexdigest()
    salary=raw.get("salary") if isinstance(raw.get("salary"),dict) else {}
    posted_at=raw.get("posted_at")
    if isinstance(posted_at,str):
        try:
            posted_at=datetime.fromisoformat(posted_at.replace("Z","+00:00"))
            if posted_at.tzinfo is None:
                posted_at=posted_at.replace(tzinfo=timezone.utc)
        except ValueError:
            posted_at=None
    else:
        posted_at=None
    salary_min=salary.get("min") if isinstance(salary.get("min"),(int,float)) else None
    salary_max=salary.get("max") if isinstance(salary.get("max"),(int,float)) else None
    currency=salary.get("currency") if isinstance(salary.get("currency"),str) and len(salary["currency"])==3 else None
    employment=raw.get("employment_type") if isinstance(raw.get("employment_type"),str) else None
    content={"company":job.company,"title":job.title,"location":job.location,
             "description":job.description,"canonical_url":job.canonical_url,
             "source":board_name,"external_id":job.external_id}
    inserted=conn.execute("""INSERT INTO job(id,source_id,company,title,description,city,province,work_mode,
          employment_type,salary_min,salary_max,currency,canonical_url,external_id,posting_fingerprint,posted_at,
          description_fingerprint,requirements,source_evidence)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
          ON CONFLICT DO NOTHING RETURNING id""",
          (identity,source_id,job.company,job.title,job.description,display_city(job.location),province,
           scope["work_mode"],employment,salary_min,salary_max,currency,job.canonical_url,job.external_id,job.fingerprint,posted_at,desc_hash,
           Jsonb({"category":scope["category"],"region":scope["province"],"scope_reviewed":"TARGET_ROLE_AND_GEOGRAPHY"}),
           Jsonb({"source":board_name,"untrusted":True,"retrieved_at":datetime.now(timezone.utc).isoformat(),
             "posted_at":posted_at.isoformat() if posted_at else None,
             "source_location":raw.get("source_location")}))).fetchone()
    if not inserted:
        return False, None
    conn.execute("INSERT INTO job_snapshot(id,job_id,content_hash,content) VALUES (%s,%s,%s,%s)",
                 (uuid4(),identity,desc_hash,Jsonb(content)))
    conn.execute("""INSERT INTO audit_event(id,actor,action,object_type,object_id,request_id,result,metadata)
      VALUES (%s,'worker','JOB_DISCOVERED','job',%s,%s,'ACCEPTED',%s)""",
      (uuid4(),identity,uuid4(),Jsonb({"source":board_name,"content_hash":desc_hash})))
    return True, identity


def _score_new_job(conn, job_id) -> bool:
    """Score only with private facts explicitly marked as resume or owner verified."""
    profile=conn.execute("SELECT id FROM candidate_profile ORDER BY created_at LIMIT 1").fetchone()
    if not profile:
        return False
    key_path=__import__("os").getenv("DOCUMENT_ENCRYPTION_KEY_FILE")
    if not key_path:
        return False
    from .storage import decrypt_document, load_key_file
    key=load_key_file(key_path)
    rows=conn.execute("""SELECT id,fact_ciphertext FROM candidate_fact
      WHERE profile_id=%s AND status IN ('RESUME_VERIFIED','OWNER_APPROVED')
      AND sensitivity='NORMAL' LIMIT 500""",(profile["id"],)).fetchall()
    if not rows:
        return False
    fact_text=[decrypt_document(row["fact_ciphertext"],key,row["id"]).decode("utf-8").casefold() for row in rows]
    verified_skills={skill for skill,aliases in SKILL_ALIASES.items()
                     if any(alias in text for alias in aliases for text in fact_text)}
    job=conn.execute("SELECT title,description,province,city,work_mode,requirements FROM job WHERE id=%s",(job_id,)).fetchone()
    if not job:
        return False
    description=(job["title"]+" "+job["description"]).casefold()
    required=[skill for skill,aliases in SKILL_ALIASES.items()
              if any(alias in description for alias in aliases)]
    if not required:
        required=["support"]
    # The discovery filter already establishes target role/region. Keep those
    # facts visible as fit dimensions while leaving years, language, legal and
    # certification declarations unknown until their source is owner-reviewed.
    blockers=[]
    extra_missing=[]
    # Never treat an unverified language declaration as satisfied. The job
    # remains visible for review but is kept out of the qualified shortlist.
    if _requires_unverified_french(description):
        blockers.append("French/bilingual requirement is not confirmed by an owner-reviewed profile fact.")
    years=re.search(r"\b(\d{1,2})\+?\s+years?\s+of\s+(?:relevant\s+)?(?:[a-z][a-z /&-]{0,40}\s+)?experience\b",description)
    if years:
        extra_missing.append(f"Posting requests {years.group(1)}+ years of experience; duration is not confirmed in the reviewed profile.")
    result=score_job(description,verified_skills,requirements=required,blockers=blockers,
        seniority_fit=True,location_fit=True,province_fit=True,work_mode_fit=True)
    conn.execute("""INSERT INTO job_match(id,job_id,profile_id,score,confidence,matched,missing,
      hard_blockers,explanation,recommended_action) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
      ON CONFLICT(job_id,profile_id) DO UPDATE SET score=excluded.score,confidence=excluded.confidence,
      matched=excluded.matched,missing=excluded.missing,hard_blockers=excluded.hard_blockers,
      explanation=excluded.explanation,recommended_action=excluded.recommended_action,created_at=now()""",
      (uuid4(),job_id,profile["id"],result.score,result.confidence,Jsonb(result.matched),
       Jsonb([*result.missing,*extra_missing]),Jsonb(result.blockers),result.explanation,result.recommendation))
    if result.score>=65 and not result.blockers:
        conn.execute("UPDATE job SET lifecycle='QUALIFIED',updated_at=now() WHERE id=%s AND lifecycle='DISCOVERED'",
                     (job_id,))
    else:
        # A re-score may discover a newly explicit blocker (for example a
        # language requirement). Withdraw only an un-applied qualification;
        # never rewrite a job already linked to an application.
        conn.execute("""UPDATE job SET lifecycle='DISCOVERED',updated_at=now()
          WHERE id=%s AND lifecycle='QUALIFIED'
          AND NOT EXISTS (SELECT 1 FROM application WHERE application.job_id=job.id)""",(job_id,))
    return True


def _requires_unverified_french(description: str) -> bool:
    return bool(re.search(
        r"\bbilingual\b|\bfrench\s+(?:fluency|proficiency)\s+(?:is\s+)?required|"
        r"\bfluent\s+in\s+french\b|\bfluency\s+in\s+french\b|"
        r"\bfluent\s+in\s+(?:both\s+)?french\s+and\s+english\b",
        description.casefold(),
    ))


@celery_app.task(name="bruteapply.discover_enabled_sources", bind=True,
                 autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=5)
def discover_enabled_sources(self):
    """Run one scheduled scan of sources with explicit permission and terms review."""
    _ensure_db()
    with db.connection() as conn:
        with conn.transaction():
            control=conn.execute("SELECT enabled,paused,drain FROM agent_configuration WHERE agent='discovery'").fetchone()
            if not control or not control["enabled"] or control["paused"] or control["drain"]:
                return {"state":"PAUSED","sources":0,"jobs":0}
            sources=conn.execute("""SELECT id,name,configuration FROM job_source
              WHERE enabled AND permitted AND terms_reviewed_at IS NOT NULL
              AND source_type='API' ORDER BY name LIMIT 50""").fetchall()
    results=[]
    for source in sources:
        slot=datetime.now(ZoneInfo("America/Toronto")).strftime("%Y-%m-%d:%H")
        identity=f"discovery:{source['id']}:{slot}"
        with db.connection() as conn:
            with conn.transaction():
                if not _claim_task(conn,identity,"public_ats_discovery"):
                    results.append({"source":source["name"],"state":"ALREADY_PROCESSED","jobs":0})
                    continue
        try:
            cfg=source["configuration"] or {}
            board_name=cfg.get("provider")
            slug=cfg.get("board_slug")
            host={"greenhouse":"boards-api.greenhouse.io","lever":"api.lever.co",
                  "smartrecruiters":"api.smartrecruiters.com"}.get(board_name)
            if not host or not isinstance(slug,str):
                raise ValueError("source configuration is incomplete")
            provider=PublicATSProvider(PublicBoard(board_name,host,slug,cfg.get("company_name")))
            raw_items=asyncio.run(provider.discover())
            created=0;duplicates=0
            with db.connection() as conn:
                with conn.transaction():
                    # Check source still enabled after network access. Revocation
                    # during the request prevents persistence.
                    live=conn.execute("SELECT enabled,permitted,terms_reviewed_at FROM job_source WHERE id=%s FOR SHARE",
                                      (source["id"],)).fetchone()
                    if not live or not live["enabled"] or not live["permitted"] or not live["terms_reviewed_at"]:
                        raise RuntimeError("source permission was revoked")
                    for raw in raw_items:
                        try:
                            added,job_id=_insert_job(conn,source["id"],source["name"],raw)
                            created += int(added)
                            duplicates += int(not added and job_id is not None)
                            if added and job_id:
                                _score_new_job(conn,job_id)
                        except ValueError:
                            # Invalid individual posting is isolated; the worker
                            # never follows its URL or executes its content.
                            continue
                    _finish_task(conn,identity,payload={"jobs_inserted":created,"duplicates":duplicates})
            results.append({"source":source["name"],"state":"SUCCEEDED","jobs":created})
        except Exception as exc:
            # Error text may contain external content; store only a category.
            category=type(exc).__name__[:80]
            with db.connection() as conn:
                with conn.transaction(): _finish_task(conn,identity,"FAILED",category)
            logger.warning("public ATS discovery failed source_id=%s category=%s",source["id"],category)
            raise
    return {"state":"SUCCEEDED","sources":len(results),"results":results}


@celery_app.task(bind=True,name="bruteapply.match_enabled_jobs",max_retries=3,default_retry_delay=60)
def match_enabled_jobs(self):
    """Re-score the existing job catalogue using verified normal resume facts."""
    _ensure_db()
    with db.connection() as conn:
        state=conn.execute("SELECT enabled,paused,drain FROM agent_configuration WHERE agent='matching'").fetchone()
    if not state or not state["enabled"] or state["paused"] or state["drain"]:
        return {"state":"PAUSED","jobs_scored":0}
    slot=datetime.now(ZoneInfo("America/Toronto")).strftime("%Y-%m-%d:") + str(datetime.now(ZoneInfo("America/Toronto")).hour//4)
    identity=f"matching:{slot}"
    with db.connection() as conn:
        with conn.transaction():
            if not _claim_task(conn,identity,"job_matching"):
                return {"state":"ALREADY_PROCESSED","jobs_scored":0}
            job_ids=[row["id"] for row in conn.execute("SELECT id FROM job ORDER BY discovered_at DESC LIMIT 500").fetchall()]
    scored=0
    try:
        for job_id in job_ids:
            with db.connection() as conn:
                with conn.transaction():
                    scored+=int(_score_new_job(conn,job_id))
        with db.connection() as conn:
            with conn.transaction(): _finish_task(conn,identity,payload={"jobs_scored":scored})
    except Exception as exc:
        category=type(exc).__name__[:80]
        with db.connection() as conn:
            with conn.transaction(): _finish_task(conn,identity,"FAILED",category,{"jobs_scored":scored})
        logger.warning("job matching failed category=%s",category)
        raise self.retry(exc=RuntimeError(category),countdown=min(60*(2**self.request.retries),1800))
    return {"state":"SUCCEEDED","jobs_scored":scored}


@celery_app.task(bind=True,name="bruteapply.sync_google_sheet",max_retries=5,default_retry_delay=60)
def sync_google_sheet(self, max_records: int | None = None):
    _ensure_db()
    task_id=str(self.request.id or uuid4())
    try:
        with db.connection() as conn:
            state=conn.execute("SELECT enabled,paused,drain FROM agent_configuration WHERE agent='sheet_sync'").fetchone()
        if not state or not state["enabled"] or state["paused"]:
            return {"state":"PAUSED","rows_created":0,"rows_updated":0,"conflicts":0}
        from .integrations.sheet_sync_runner import run_sync
        result=run_sync(task_id,max_records=max_records)
        with db.connection() as conn:
            with conn.transaction():
                conn.execute("""INSERT INTO worker_job(id,idempotency_key,task_name,state,attempts,started_at,completed_at,payload)
                  VALUES (%s,%s,'google_sheet_sync','SUCCEEDED',%s,now(),now(),%s)
                  ON CONFLICT(idempotency_key) DO UPDATE SET state='SUCCEEDED',attempts=EXCLUDED.attempts,
                  completed_at=now(),error_category=NULL,payload=EXCLUDED.payload""",
                  (uuid4(),f"google-sheet:{task_id}",self.request.retries+1,Jsonb(result)))
        return {"state":"SUCCEEDED",**result}
    except Exception as exc:
        category=type(exc).__name__[:80]
        try:
            with db.connection() as conn:
                with conn.transaction():
                    integration=conn.execute("""INSERT INTO integration(id,provider,state,credential_reference,scopes,last_error_category)
                      VALUES (%s,'google_sheets','ERROR','/run/secrets/google_oauth',%s,%s)
                      ON CONFLICT(provider) DO UPDATE SET state='ERROR',last_error_category=EXCLUDED.last_error_category,updated_at=now()
                      RETURNING id""",(uuid4(),Jsonb(["spreadsheets"]),category)).fetchone()
                    terminal=self.request.retries>=self.max_retries
                    conn.execute("""INSERT INTO worker_job(id,idempotency_key,task_name,state,attempts,started_at,
                      completed_at,error_category) VALUES (%s,%s,'google_sheet_sync',%s,%s,now(),
                      CASE WHEN %s THEN now() ELSE NULL END,%s)
                      ON CONFLICT(idempotency_key) DO UPDATE SET state=EXCLUDED.state,attempts=EXCLUDED.attempts,
                      completed_at=EXCLUDED.completed_at,error_category=EXCLUDED.error_category""",
                      (uuid4(),f"google-sheet:{task_id}","DEAD_LETTER" if terminal else "RETRY",
                       self.request.retries+1,terminal,category))
                    conn.execute("""INSERT INTO sync_state(id,integration_id,resource,last_attempt_at,retry_count,dead_letter_count,error_category)
                      VALUES (%s,%s,'APPLICATIONS',now(),%s,%s,%s)
                      ON CONFLICT(integration_id,resource) DO UPDATE SET last_attempt_at=now(),retry_count=EXCLUDED.retry_count,
                      dead_letter_count=sync_state.dead_letter_count+EXCLUDED.dead_letter_count,error_category=EXCLUDED.error_category""",
                      (uuid4(),integration["id"],self.request.retries+1,1 if terminal else 0,category))
                    conn.execute("UPDATE integration SET state='ERROR',last_error_category=%s,updated_at=now() WHERE provider='google_sheets'",(category,))
        except Exception:
            pass
        logger.warning("google sheet sync failed category=%s retry=%s",category,self.request.retries)
        if self.request.retries<self.max_retries:
            raise self.retry(exc=RuntimeError(category),countdown=min(60*(2**self.request.retries),1800))
        raise


@celery_app.task(bind=True,name="bruteapply.prepare_documents",max_retries=3,default_retry_delay=60)
def prepare_documents(self):
    """Export already-provenanced private drafts to PDF and editable DOCX."""
    _ensure_db()
    with db.connection() as conn:
        state=conn.execute("SELECT enabled,paused,drain FROM agent_configuration WHERE agent='documents'").fetchone()
    if not state or not state["enabled"] or state["paused"] or state["drain"]:
        return {"state":"PAUSED","source_documents":0,"artifacts_created":0}
    identity="document-export:v1"
    orphan_paths=[]
    try:
        from .domain.document_export import render_docx, render_pdf
        from .storage import decrypt_document, load_key_file, store_private_blob
        from . import config
        key=load_key_file(__import__("os").getenv("DOCUMENT_ENCRYPTION_KEY_FILE",""))
        with db.connection() as conn:
            sources=conn.execute("""SELECT d.id,d.profile_id,d.application_id,d.job_id,d.document_type,d.sha256,d.size_bytes,
              d.storage_key,d.resume_base_id,d.provenance FROM document d
              WHERE d.encrypted AND d.mime_type='text/plain'
                AND d.document_type IN ('TAILORED_RESUME','COVER_LETTER','SCREENING_RESPONSE','APPLICATION_SUMMARY')
                AND (d.application_id IS NOT NULL OR d.job_id IS NOT NULL)
              ORDER BY d.created_at DESC LIMIT 40""").fetchall()
        created=0; processed=0
        for source in sources:
            with db.connection() as conn:
                with conn.transaction():
                    locked=conn.execute("SELECT id FROM document WHERE id=%s FOR UPDATE",(source["id"],)).fetchone()
                    existing={row["format"] for row in conn.execute(
                      "SELECT provenance->>'format' AS format FROM document WHERE provenance->>'source_document_id'=%s",
                      (str(source["id"]),)).fetchall()}
                    missing=[fmt for fmt in ("pdf","docx") if fmt not in existing]
                    if not locked or not missing: continue
                    if not re.fullmatch(r"[0-9a-f]{2}/[0-9a-f]{32}\.txt\.enc",source["storage_key"]):
                        continue
                    path=config.DOCUMENT_ROOT/source["storage_key"]
                    if path.is_symlink() or not path.is_file(): continue
                    encrypted=path.read_bytes()
                    text_bytes=decrypt_document(encrypted,key,source["id"])
                    if hashlib.sha256(text_bytes).hexdigest()!=source["sha256"] or len(text_bytes)!=source["size_bytes"]:
                        continue
                    text=text_bytes.decode("utf-8")
                    for fmt in missing:
                        data=render_pdf(text) if fmt=="pdf" else render_docx(text)
                        new_id,storage_key,sha=store_private_blob(data,fmt,config.DOCUMENT_ROOT,key)
                        orphan_paths.append(storage_key)
                        mime="application/pdf" if fmt=="pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                        provenance={**(source["provenance"] or {}),"format":fmt,"source_document_id":str(source["id"]),
                          "generator":"provenance-preserving-export-v1"}
                        conn.execute("""INSERT INTO document(id,profile_id,application_id,job_id,document_type,sha256,mime_type,
                          size_bytes,storage_key,encrypted,resume_base_id,provenance)
                          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,true,%s,%s)""",
                          (new_id,source["profile_id"],source["application_id"],source["job_id"],source["document_type"],
                           sha,mime,len(data),storage_key,source["resume_base_id"],Jsonb(provenance)))
                        conn.execute("""INSERT INTO audit_event(id,actor,action,object_type,object_id,request_id,result,metadata)
                          VALUES (%s,'document-worker','PRIVATE_DOCUMENT_EXPORTED','document',%s,%s,%s,%s)""",
                          (uuid4(),new_id,uuid4(),source["document_type"],Jsonb({"format":fmt,"source_document_id":str(source["id"])})))
                        created+=1
                    processed+=1
            # This source's rows committed successfully. Keep their encrypted
            # files and reserve cleanup only for a later failed transaction.
            orphan_paths.clear()
        with db.connection() as conn:
            with conn.transaction():
                conn.execute("""INSERT INTO worker_job(id,idempotency_key,task_name,state,attempts,started_at,completed_at,payload)
                  VALUES (%s,%s,'document_export','SUCCEEDED',1,now(),now(),%s)
                  ON CONFLICT(idempotency_key) DO UPDATE SET state='SUCCEEDED',attempts=worker_job.attempts+1,
                  completed_at=now(),error_category=NULL,payload=EXCLUDED.payload""",
                  (uuid4(),identity,Jsonb({"source_documents":processed,"artifacts_created":created})))
        return {"state":"SUCCEEDED","source_documents":processed,"artifacts_created":created}
    except Exception as exc:
        for storage_key in orphan_paths:
            try:
                (config.DOCUMENT_ROOT/storage_key).unlink(missing_ok=True)
            except OSError:
                pass
        category=type(exc).__name__[:80]
        logger.warning("private document export failed category=%s",category)
        raise self.retry(exc=RuntimeError(category),countdown=min(60*(2**self.request.retries),1800))


@celery_app.task(name="bruteapply.submit_application")
def submit_application(application_id: str):
    raise RuntimeError("real submission handlers are not configured; submission remains disabled")


@celery_app.task(name="bruteapply.process_email")
def process_email():
    raise RuntimeError("email OAuth is not configured")


@celery_app.task(name="bruteapply.process_followups")
def process_followups():
    raise RuntimeError("calendar/email authorization is not configured")
