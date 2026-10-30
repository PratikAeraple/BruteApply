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
from uuid import uuid4

from celery import Celery
from celery.schedules import crontab
from psycopg.types.json import Jsonb

from . import config, db
from .domain.jobs import normalize_job
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
    task_default_retry_delay=60, task_max_retries=5,
    beat_schedule={"schedule-permitted-public-discovery": {
        "task": "bruteapply.discover_enabled_sources",
        "schedule": crontab(minute=15, hour=7),
    }},
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


def _claim_task(conn, key: str, name: str) -> bool:
    row = conn.execute("""INSERT INTO worker_job(id,idempotency_key,task_name,state,attempts,started_at)
      VALUES (%s,%s,%s,'RUNNING',1,now()) ON CONFLICT(idempotency_key) DO UPDATE
      SET state='RUNNING',attempts=worker_job.attempts+1,started_at=now(),error_category=NULL
      WHERE (worker_job.state IN ('FAILED','RETRY') OR
        (worker_job.state='RUNNING' AND worker_job.started_at<now()-interval '1 hour'))
        AND worker_job.attempts<5
      RETURNING id""", (uuid4(), key, name)).fetchone()
    return row is not None


def _finish_task(conn, key: str, state: str = "SUCCEEDED", error: str | None = None) -> None:
    conn.execute("""UPDATE worker_job SET state=CASE WHEN %s='FAILED' AND attempts>=5 THEN 'DEAD_LETTER' ELSE %s END,
      completed_at=now(),error_category=%s
      WHERE idempotency_key=%s""", (state, state, error, key))


def _insert_job(conn, source_id, board_name: str, raw: dict) -> tuple[bool, object | None]:
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
    content={"company":job.company,"title":job.title,"location":job.location,
             "description":job.description,"canonical_url":job.canonical_url,
             "source":board_name,"external_id":job.external_id}
    inserted=conn.execute("""INSERT INTO job(id,source_id,company,title,description,city,province,work_mode,
          canonical_url,external_id,posting_fingerprint,description_fingerprint,source_evidence)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
          ON CONFLICT DO NOTHING RETURNING id""",
          (identity,source_id,job.company,job.title,job.description,job.location,job.province,
           job.work_mode,job.canonical_url,job.external_id,job.fingerprint,desc_hash,
           Jsonb({"source":board_name,"untrusted":True,"retrieved_at":datetime.now(timezone.utc).isoformat()}))).fetchone()
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
    job=conn.execute("SELECT description,province,city FROM job WHERE id=%s",(job_id,)).fetchone()
    if not job:
        return False
    result=score_job(job["description"],verified_skills)
    conn.execute("""INSERT INTO job_match(id,job_id,profile_id,score,confidence,matched,missing,
      hard_blockers,explanation,recommended_action) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
      ON CONFLICT(job_id,profile_id) DO UPDATE SET score=excluded.score,confidence=excluded.confidence,
      matched=excluded.matched,missing=excluded.missing,hard_blockers=excluded.hard_blockers,
      explanation=excluded.explanation,recommended_action=excluded.recommended_action,created_at=now()""",
      (uuid4(),job_id,profile["id"],result.score,result.confidence,Jsonb(result.matched),
       Jsonb(result.missing),Jsonb(result.blockers),result.explanation,result.recommendation))
    if result.score>=65 and not result.blockers:
        conn.execute("UPDATE job SET lifecycle='QUALIFIED',updated_at=now() WHERE id=%s AND lifecycle='DISCOVERED'",
                     (job_id,))
    return True


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
        identity=f"discovery:{source['id']}:{datetime.now(timezone.utc).date().isoformat()}"
        with db.connection() as conn:
            with conn.transaction():
                if not _claim_task(conn,identity,"public_ats_discovery"):
                    results.append({"source":source["name"],"state":"ALREADY_PROCESSED","jobs":0})
                    continue
        try:
            cfg=source["configuration"] or {}
            board_name=cfg.get("provider")
            slug=cfg.get("board_slug")
            host={"greenhouse":"boards-api.greenhouse.io","lever":"api.lever.co"}.get(board_name)
            if not host or not isinstance(slug,str):
                raise ValueError("source configuration is incomplete")
            provider=PublicATSProvider(PublicBoard(board_name,host,slug))
            raw_items=asyncio.run(provider.discover())
            created=0
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
                            if added and job_id:
                                _score_new_job(conn,job_id)
                        except ValueError:
                            # Invalid individual posting is isolated; the worker
                            # never follows its URL or executes its content.
                            continue
                    _finish_task(conn,identity)
            results.append({"source":source["name"],"state":"SUCCEEDED","jobs":created})
        except Exception as exc:
            # Error text may contain external content; store only a category.
            category=type(exc).__name__[:80]
            with db.connection() as conn:
                with conn.transaction(): _finish_task(conn,identity,"FAILED",category)
            logger.warning("public ATS discovery failed source_id=%s category=%s",source["id"],category)
            raise
    return {"state":"SUCCEEDED","sources":len(results),"results":results}


@celery_app.task(name="bruteapply.sync_google_sheet")
def sync_google_sheet():
    raise RuntimeError("Google Sheets worker is disabled until owner OAuth, snapshot, and mapping validation")


@celery_app.task(name="bruteapply.prepare_documents")
def prepare_documents():
    raise RuntimeError("Document job queue is not activated; generation remains owner initiated")


@celery_app.task(name="bruteapply.submit_application")
def submit_application(application_id: str):
    raise RuntimeError("real submission handlers are not configured; submission remains disabled")


@celery_app.task(name="bruteapply.process_email")
def process_email():
    raise RuntimeError("email OAuth is not configured")


@celery_app.task(name="bruteapply.process_followups")
def process_followups():
    raise RuntimeError("calendar/email authorization is not configured")
