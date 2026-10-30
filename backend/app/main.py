from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from uuid import UUID, uuid4
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb

from . import config, db
from .auth import Principal, require_owner
from .domain.jobs import normalize_job
from .domain.policy import ApplicationState, SubmissionContext, submission_decision, transition
from .domain.tickets import transition_ticket
from .domain.profile import merge_profile_facts, validate_profile_facts
from .domain.fact_review import FactStatus, ResumeFact, can_bulk_transition, validate_resume_fact
from .storage import MAX_UPLOAD_BYTES, load_key_file, safe_display_name, store_private, validate_pdf
from .rate_limit import enforce_owner_limit


DEMO_OVERVIEW = {
    "mode": "DEMO",
    "counts": {"discovered": 18, "qualified": 7, "shortlisted": 3, "prepared": 2,
               "submitted": 0, "unverified": 0, "tickets": 2, "interviews": 0, "offers": 0},
    "workers": {"discovery": "PAUSED", "matching": "READY", "documents": "READY",
                "submission": "DISABLED", "sheet_sync": "NOT CONNECTED", "email": "NOT CONNECTED"},
    "integrations": {"google_sheets": "NOT CONFIGURED", "email": "NOT CONFIGURED", "ai": "FAKE PROVIDER"},
    "backup": {"status": "DEFERRED / NOT IMPLEMENTED", "restore_test": "NOT TESTED"},
    "components": {"database":"SYNTHETIC", "queue":"SYNTHETIC", "worker":"NOT RUNNING"},
}
DEMO_JOBS = [
    {"id":"demo-job-01","company":"Northwind Community Services","title":"IT Support Technician","city":"Toronto","province":"Ontario","work_mode":"Hybrid","category":"L1 / Help Desk","score":88,"confidence":0.79,"matched":["support","Linux","networking"],"missing":["Active Directory"],"blockers":[],"action":"REVIEW","source":"Company Website","posted":"2026-10-27","url":"https://example.invalid/jobs/it-support-01"},
    {"id":"demo-job-02","company":"Maple Systems Cooperative","title":"Junior Systems Administrator","city":"Winnipeg","province":"Manitoba","work_mode":"On-site","category":"Systems / Infrastructure","score":81,"confidence":0.75,"matched":["Linux","Docker","monitoring"],"missing":["Active Directory"],"blockers":[],"action":"REVIEW","source":"Greenhouse","posted":"2026-10-26","url":"https://example.invalid/jobs/junior-sysadmin-02"},
    {"id":"demo-job-03","company":"Prairie Technical Services","title":"Endpoint Support Analyst","city":"Calgary","province":"Alberta","work_mode":"Field / Multi-site","category":"L2 / Desktop / Technical","score":76,"confidence":0.66,"matched":["support","networking"],"missing":["Active Directory","cloud"],"blockers":[],"action":"REVIEW","source":"Lever","posted":"2026-10-24","url":"https://example.invalid/jobs/endpoint-03"},
]
DEMO_TICKETS = [
    {"id":"BA-DEMO-1042","category":"SENSITIVE_QUESTION","priority":"HIGH","status":"OPEN","summary":"Employer asks for a sensitive declaration","required_action":"Review the question and provide an approved answer or skip."},
    {"id":"BA-DEMO-1041","category":"MANUAL_SUBMISSION_REQUIRED","priority":"NORMAL","status":"OPEN","summary":"Application provider requires an interactive account step","required_action":"Open the employer site and complete it manually if desired."},
]
RUNNABLE_WORKERS: set[str] = {"discovery"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.validate_production_config()
    if not config.DEMO_MODE:
        db.start_pool()
    yield
    db.close_pool()


app = FastAPI(title="BruteApply API", version="0.1.0", docs_url=None, redoc_url=None,
              openapi_url=None, lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "api", "jobapply.brutehack.com", "testserver"])


@app.middleware("http")
async def security_headers(request: Request, call_next):
    if not config.DEMO_MODE and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        if origin != "https://jobapply.brutehack.com":
            return JSONResponse(status_code=403, content={"detail":"request origin rejected"})
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    return response


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "application": "bruteapply", "mode": "DEMO" if config.DEMO_MODE else "PRODUCTION",
            "revision": config.SOURCE_REVISION}


@app.get("/readyz")
async def readyz():
    if config.DEMO_MODE:
        return {"status": "ready", "application": "bruteapply-demo"}
    if not db.ready():
        raise HTTPException(503, "not ready")
    return {"status": "ready", "application": "bruteapply", "revision": config.SOURCE_REVISION}


def _owner(principal: Principal = Depends(require_owner)) -> Principal:
    return principal


@app.get("/api/v1/overview")
async def overview(_: Principal = Depends(_owner)):
    if config.DEMO_MODE:
        return DEMO_OVERVIEW
    with db.connection() as conn:
        app_counts = conn.execute("SELECT state,count(*)::int AS count FROM application GROUP BY state").fetchall()
        job_counts = conn.execute("SELECT lifecycle,count(*)::int AS count FROM job GROUP BY lifecycle").fetchall()
        tickets = conn.execute("SELECT count(*)::int FROM ticket WHERE state IN ('OPEN','IN_PROGRESS')").fetchone()[0]
        jobs = conn.execute("SELECT count(*)::int FROM job").fetchone()[0]
        agent_rows=conn.execute("SELECT agent,enabled,paused,drain,emergency_stopped FROM agent_configuration").fetchall()
    result = {"mode":"PRODUCTION", "counts":{"discovered":jobs,"tickets":tickets},
              "components":{"database":"UP","queue":"UNKNOWN","worker":"UNKNOWN"},
              "workers":{}}
    result["counts"].update({r["lifecycle"].casefold():r["count"] for r in job_counts})
    result["counts"].update({r["state"].casefold():r["count"] for r in app_counts})
    result["workers"]={r["agent"]:("EMERGENCY STOP" if r["emergency_stopped"] else "NOT CONFIGURED"
                                  if r["agent"] not in RUNNABLE_WORKERS else "PAUSED" if r["paused"]
                                  else "ENABLED" if r["enabled"] else "STOPPED") for r in agent_rows}
    try:
        import redis
        client=redis.Redis.from_url(config.REDIS_URL,socket_connect_timeout=1,socket_timeout=1)
        client.ping(); client.close()
        result["components"]["queue"]="UP"
    except Exception:
        result["components"]["queue"]="DOWN"
    result["backup"] = {"status":"DEFERRED / NOT IMPLEMENTED", "restore_test":"NOT TESTED"}
    result["integrations"] = {"google_sheets":"NOT CONFIGURED", "email":"NOT CONFIGURED", "ai":"NOT CONFIGURED"}
    return result


@app.get("/api/v1/jobs")
async def jobs(_: Principal = Depends(_owner)):
    if config.DEMO_MODE:
        return {"items": DEMO_JOBS}
    with db.connection() as conn:
        rows = conn.execute("""SELECT j.id::text,j.company,j.title,j.city,j.province,j.work_mode,j.lifecycle,
          COALESCE(j.requirements->>'category','Uncategorized') AS category,
          COALESCE(m.score,0)::int AS score,COALESCE(m.confidence,0)::float AS confidence,
          COALESCE(m.matched,'[]'::jsonb) AS matched,COALESCE(m.missing,'[]'::jsonb) AS missing,
          COALESCE(m.hard_blockers,'[]'::jsonb) AS blockers,
          COALESCE(m.recommended_action,'REVIEW') AS action,
          s.name AS source,j.posted_at AS posted,j.canonical_url AS url,j.discovered_at
          FROM job j LEFT JOIN job_source s ON s.id=j.source_id
          LEFT JOIN job_match m ON m.job_id=j.id
          ORDER BY j.discovered_at DESC LIMIT 200""").fetchall()
    return {"items": rows}


@app.get("/api/v1/tickets")
async def tickets(_: Principal = Depends(_owner)):
    if config.DEMO_MODE:
        return {"items": DEMO_TICKETS}
    with db.connection() as conn:
        rows = conn.execute("""SELECT id::text,category,priority,state,summary_ciphertext,evidence_ciphertext,
          required_action_ciphertext,created_at,updated_at
          FROM ticket WHERE state IN ('OPEN','IN_PROGRESS') ORDER BY priority DESC,created_at LIMIT 250""").fetchall()
    from .storage import decrypt_document
    key=_document_key(); result=[]
    for row in rows:
        identity=UUID(row["id"])
        try:
            summary=decrypt_document(row["summary_ciphertext"],key,identity).decode()
            action=decrypt_document(row["required_action_ciphertext"],key,identity).decode()
            evidence=json.loads(decrypt_document(row["evidence_ciphertext"],key,identity))
        except Exception:
            raise HTTPException(503,"private ticket data unavailable") from None
        result.append({"id":row["id"],"category":row["category"],"priority":row["priority"],"status":row["state"],
                       "summary":summary,"required_action":action,"evidence":evidence,
                       "created_at":row["created_at"],"updated_at":row["updated_at"]})
    return {"items":result}


@app.get("/api/v1/applications")
async def applications(_: Principal = Depends(_owner)):
    if config.DEMO_MODE:
        return {"items":[]}
    with db.connection() as conn:
        rows=conn.execute("""SELECT a.id::text,a.state,a.created_at,a.updated_at,a.date_applied,
          j.id::text AS job_id,j.company,j.title,j.city,j.province,j.work_mode,j.canonical_url,
          m.score,m.confidence FROM application a JOIN job j ON j.id=a.job_id
          LEFT JOIN job_match m ON m.job_id=j.id ORDER BY a.updated_at DESC LIMIT 500""").fetchall()
    return {"items":rows}


@app.get("/api/v1/analytics")
async def analytics(_: Principal = Depends(_owner)):
    if config.DEMO_MODE:
        return {"mode":"DEMO","available":False,"reason":"no production history in demo mode"}
    with db.connection() as conn:
        jobs=conn.execute("""SELECT date_trunc('week',discovered_at)::date AS week,count(*)::int AS count
          FROM job WHERE discovered_at>=now()-interval '12 weeks' GROUP BY 1 ORDER BY 1""").fetchall()
        funnel=conn.execute("""SELECT 'DISCOVERED' AS stage,count(*)::int AS count FROM job
          UNION ALL SELECT 'QUALIFIED',count(*)::int FROM job WHERE lifecycle IN ('QUALIFIED','SHORTLISTED','PREPARED')
          UNION ALL SELECT 'SHORTLISTED',count(*)::int FROM application
          UNION ALL SELECT 'PREPARED',count(*)::int FROM application WHERE state IN ('PREPARED','READY','SUBMISSION_PENDING','ATTEMPTED','SUBMISSION_ATTEMPTED','SUBMITTED','SUBMISSION_UNVERIFIED')
          UNION ALL SELECT 'SUBMITTED',count(*)::int FROM application WHERE state IN ('SUBMITTED','ASSESSMENT','RECRUITER_SCREEN','INTERVIEW_1','INTERVIEW_2','FINAL_INTERVIEW','OFFER')""").fetchall()
        score=conn.execute("""SELECT CASE WHEN score<40 THEN '0-39' WHEN score<60 THEN '40-59'
          WHEN score<80 THEN '60-79' ELSE '80-100' END AS band,count(*)::int AS count
          FROM job_match GROUP BY 1 ORDER BY 1""").fetchall()
        sources=conn.execute("""SELECT s.name AS source,count(DISTINCT j.id)::int AS discovered,
          count(DISTINCT a.id)::int AS applications,count(DISTINCT a.id) FILTER (WHERE a.state IN
          ('SUBMITTED','ASSESSMENT','RECRUITER_SCREEN','INTERVIEW_1','INTERVIEW_2','FINAL_INTERVIEW','OFFER'))::int AS submitted
          FROM job_source s LEFT JOIN job j ON j.source_id=s.id LEFT JOIN application a ON a.job_id=j.id
          GROUP BY s.name ORDER BY discovered DESC LIMIT 20""").fetchall()
        categories=conn.execute("""SELECT COALESCE(j.requirements->>'category','Uncategorized') AS category,
          count(DISTINCT j.id)::int AS jobs,count(DISTINCT a.id)::int AS applications
          FROM job j LEFT JOIN application a ON a.job_id=j.id GROUP BY 1 ORDER BY jobs DESC LIMIT 20""").fetchall()
        provinces=conn.execute("""SELECT COALESCE(j.province,'Unspecified') AS province,count(DISTINCT a.id)::int AS applications
          FROM job j JOIN application a ON a.job_id=j.id GROUP BY 1 ORDER BY applications DESC""").fetchall()
        events=conn.execute("SELECT state,count(*)::int AS count FROM application GROUP BY state").fetchall()
    total_apps=sum(row["count"] for row in events)
    return {"mode":"PRODUCTION","available":True,"sample_size":total_apps,
      "small_sample":total_apps<5,"jobs_by_week":jobs,"funnel":funnel,"score_distribution":score,
      "source_performance":sources,"category_performance":categories,"province_performance":provinces,
      "application_states":events}


class ApplicationCreate(BaseModel):
    job_id: UUID


@app.post("/api/v1/applications", status_code=201)
async def create_application(payload: ApplicationCreate, principal: Principal = Depends(_owner)):
    if config.DEMO_MODE:
        return {"mode":"DEMO","created":False,"reason":"demo mode has no application records"}
    enforce_owner_limit(principal,"application-create",20,3600)
    with db.connection() as conn:
        with conn.transaction():
            profile=conn.execute("SELECT id FROM candidate_profile ORDER BY created_at LIMIT 1 FOR SHARE").fetchone()
            if not profile: raise HTTPException(409,"profile setup is required")
            job=conn.execute("SELECT id,lifecycle FROM job WHERE id=%s FOR UPDATE",(payload.job_id,)).fetchone()
            if not job: raise HTTPException(404,"job not found")
            existing=conn.execute("SELECT id::text,state FROM application WHERE profile_id=%s AND job_id=%s",
                                  (profile["id"],payload.job_id)).fetchone()
            if existing: return {"id":existing["id"],"state":existing["state"],"duplicate":True}
            app_id=uuid4();key=f"shortlist:{profile['id']}:{payload.job_id}"
            conn.execute("""INSERT INTO application(id,profile_id,job_id,idempotency_key,state)
              VALUES (%s,%s,%s,%s,'SHORTLISTED')""",(app_id,profile["id"],payload.job_id,key))
            conn.execute("INSERT INTO application_event(id,application_id,from_state,to_state,actor) VALUES (%s,%s,NULL,'SHORTLISTED',%s)",
                         (uuid4(),app_id,principal.email))
            conn.execute("INSERT INTO audit_event(id,actor,action,object_type,object_id,request_id,result) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                         (uuid4(),principal.email,"APPLICATION_SHORTLISTED","application",app_id,uuid4(),"SHORTLISTED"))
    return {"id":str(app_id),"state":"SHORTLISTED","duplicate":False}


class SafeEvidence(BaseModel):
    kind: Literal["CONFIRMATION", "DOCUMENT", "EMAIL", "POSTING", "OTHER"]
    reference_sha256: str = Field(pattern="^[0-9a-f]{64}$")


class ApplicationTransition(BaseModel):
    state: ApplicationState
    evidence: list[SafeEvidence] = Field(default_factory=list, max_length=20)


@app.patch("/api/v1/applications/{application_id}")
async def update_application(application_id: UUID, payload: ApplicationTransition,
                             principal: Principal = Depends(_owner)):
    if config.DEMO_MODE:
        return {"mode":"DEMO","changed":False}
    enforce_owner_limit(principal,"application-update",60,3600)
    with db.connection() as conn:
        with conn.transaction():
            current=conn.execute("SELECT state FROM application WHERE id=%s FOR UPDATE",(application_id,)).fetchone()
            if not current: raise HTTPException(404,"application not found")
            try: target=transition(ApplicationState(current["state"]),payload.state)
            except ValueError as exc: raise HTTPException(409,str(exc)) from None
            if target is ApplicationState.SUBMITTED:
                raise HTTPException(409,"submission requires provider evidence verification workflow")
            if target is ApplicationState.ATTEMPTED:
                raise HTTPException(409,"only an authorized provider worker may record an attempt")
            conn.execute("UPDATE application SET state=%s,updated_at=now() WHERE id=%s",(target,application_id))
            conn.execute("INSERT INTO application_event(id,application_id,from_state,to_state,actor,evidence) VALUES (%s,%s,%s,%s,%s,%s)",
                         (uuid4(),application_id,current["state"],target,principal.email,Jsonb([x.model_dump() for x in payload.evidence])))
            conn.execute("INSERT INTO audit_event(id,actor,action,object_type,object_id,request_id,result) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                         (uuid4(),principal.email,"APPLICATION_STATE_CHANGED","application",application_id,uuid4(),target))
    return {"id":str(application_id),"state":target}


class TicketCreate(BaseModel):
    category: Literal["MISSING_INFORMATION", "MANUAL_SUBMISSION_REQUIRED", "AUTHENTICATION_REQUIRED",
                      "CAPTCHA_REQUIRED", "MFA_REQUIRED", "SENSITIVE_QUESTION", "AMBIGUOUS_ANSWER",
                      "DOCUMENT_EXCEPTION", "SUBMISSION_UNVERIFIED", "RECRUITER_REQUEST", "SYNC_ERROR",
                      "INTEGRATION_ERROR", "SECURITY_EVENT", "WORKFLOW_FAILURE"]
    priority: str = Field(default="NORMAL", pattern="^(LOW|NORMAL|HIGH|CRITICAL)$")
    summary: str = Field(min_length=3, max_length=240)
    required_action: str = Field(min_length=3, max_length=2000)
    job_id: UUID | None = None
    application_id: UUID | None = None
    evidence: list[SafeEvidence] = Field(default_factory=list, max_length=20)


class TicketUpdate(BaseModel):
    state: str = Field(pattern="^(OPEN|IN_PROGRESS|RESOLVED|CANCELLED)$")
    decision: Literal["APPROVE", "DENY", "PROVIDE_INFORMATION", "EDIT", "RETRY", "SKIP", "RESOLVE"] | None = None
    resolution: str | None = Field(default=None, max_length=2000)
    details: str | None = Field(default=None, max_length=5000)


@app.post("/api/v1/tickets", status_code=201)
async def create_ticket(payload: TicketCreate, principal: Principal = Depends(_owner)):
    if config.DEMO_MODE:
        return {"mode":"DEMO","created":False,"reason":"demo mode does not store private records"}
    if payload.job_id is None and payload.application_id is None and not payload.category:
        raise HTTPException(422, "ticket context is required")
    enforce_owner_limit(principal,"ticket-create",20,3600)
    ticket_id = uuid4()
    with db.connection() as conn:
        with conn.transaction():
            from .storage import encrypt_document
            key=_document_key()
            conn.execute("""INSERT INTO ticket(id,category,priority,job_id,application_id,summary_ciphertext,
              evidence_ciphertext,required_action_ciphertext) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
              (ticket_id,payload.category,payload.priority,payload.job_id,payload.application_id,
               encrypt_document(payload.summary.encode(),key,ticket_id),
               encrypt_document(json.dumps([x.model_dump() for x in payload.evidence]).encode(),key,ticket_id),
               encrypt_document(payload.required_action.encode(),key,ticket_id)))
            conn.execute("INSERT INTO ticket_event(id,ticket_id,actor,action,created_at) VALUES (%s,%s,%s,%s,now())",
                         (uuid4(),ticket_id,principal.email,"CREATED"))
            conn.execute("INSERT INTO audit_event(id,actor,action,object_type,object_id,request_id,result) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                         (uuid4(),principal.email,"TICKET_CREATED","ticket",ticket_id,uuid4(),payload.category))
    return {"id":str(ticket_id),"state":"OPEN"}


@app.patch("/api/v1/tickets/{ticket_id}")
async def update_ticket(ticket_id: UUID, payload: TicketUpdate, principal: Principal = Depends(_owner)):
    if config.DEMO_MODE:
        return {"mode":"DEMO","changed":False}
    enforce_owner_limit(principal,"ticket-update",60,3600)
    with db.connection() as conn:
        with conn.transaction():
            current = conn.execute("SELECT state,decision FROM ticket WHERE id=%s FOR UPDATE",(ticket_id,)).fetchone()
            if not current:
                raise HTTPException(404,"ticket not found")
            if current["decision"] in {"DENY","SKIP"} and payload.state in {"OPEN","IN_PROGRESS"}:
                raise HTTPException(409,"a denied or skipped workflow cannot be resumed")
            if payload.decision == "RETRY":
                raise HTTPException(409,"no retryable workflow handler is registered for this ticket")
            requested=payload.state
            if payload.decision in {"APPROVE","PROVIDE_INFORMATION","EDIT"}: requested="IN_PROGRESS"
            elif payload.decision in {"DENY","SKIP"}: requested="CANCELLED"
            elif payload.decision == "RESOLVE": requested="RESOLVED"
            try:
                target = transition_ticket(current["state"],requested,decision=payload.decision)
            except ValueError as exc:
                raise HTTPException(409,str(exc)) from None
            if target == "RESOLVED" and not payload.resolution:
                raise HTTPException(422,"resolution text is required")
            if payload.decision in {"DENY","SKIP"} and not payload.resolution:
                raise HTTPException(422,"a denial/skip reason is required")
            if payload.decision in {"PROVIDE_INFORMATION","EDIT"} and not payload.details:
                raise HTTPException(422,"updated information is required")
            from .storage import encrypt_document
            resolution=encrypt_document(payload.resolution.encode(),_document_key(),ticket_id) if payload.resolution else None
            conn.execute("UPDATE ticket SET state=%s,decision=%s,resolution_ciphertext=%s,updated_at=now() WHERE id=%s",
                         (target,payload.decision,resolution,ticket_id))
            details=encrypt_document(payload.details.encode(),_document_key(),ticket_id) if payload.details else None
            conn.execute("INSERT INTO ticket_event(id,ticket_id,actor,action,detail_ciphertext,created_at) VALUES (%s,%s,%s,%s,%s,now())",
                         (uuid4(),ticket_id,principal.email,f"DECISION:{payload.decision or target}",details))
            conn.execute("INSERT INTO audit_event(id,actor,action,object_type,object_id,request_id,result) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                         (uuid4(),principal.email,"TICKET_UPDATED","ticket",ticket_id,uuid4(),target))
    return {"id":str(ticket_id),"state":target,"workflow_resumed":False}


@app.get("/api/v1/resumes")
async def list_resumes(principal: Principal = Depends(_owner)):
    if config.DEMO_MODE:
        return {"mode":"DEMO","items":[]}
    with db.connection() as conn:
        items=conn.execute("""SELECT id::text,version,label,category,sha256,mime_type,size_bytes,
          canonical,active,imported_at,page_count FROM resume ORDER BY imported_at DESC""").fetchall()
    return {"items":items}


class ResumeFactInput(BaseModel):
    ordinal: int = Field(ge=0, le=10000)
    text: str = Field(min_length=1, max_length=5000)
    page: int = Field(ge=1, le=20)
    section: str = Field(min_length=1, max_length=80)
    confidence: float = Field(ge=0, le=1)
    sensitivity: Literal["NORMAL", "SENSITIVE", "LEGAL"] = "NORMAL"


class ResumeFactImport(BaseModel):
    source_sha256: str = Field(pattern="^[0-9a-f]{64}$")
    source_revision: str = Field(min_length=7, max_length=64)
    facts: list[ResumeFactInput] = Field(min_length=1, max_length=200)


@app.post("/api/v1/resumes/{resume_id}/facts/import", status_code=201)
async def import_resume_facts(resume_id: UUID, payload: ResumeFactImport,
                              principal: Principal = Depends(_owner)):
    if config.DEMO_MODE:
        raise HTTPException(409,"demo mode does not accept personal candidate facts")
    if len({item.ordinal for item in payload.facts}) != len(payload.facts):
        raise HTTPException(422,"fact ordinals must be unique within a batch")
    with db.connection() as conn:
        with conn.transaction():
            resume=conn.execute("""SELECT id,profile_id,sha256,page_count,canonical,active
              FROM resume WHERE id=%s FOR SHARE""",(resume_id,)).fetchone()
            if not resume or not resume["canonical"] or not resume["active"]:
                raise HTTPException(404,"active canonical resume not found")
            if resume["sha256"].strip() != payload.source_sha256:
                raise HTTPException(409,"fact source does not match the active resume")
            if not resume["page_count"]:
                raise HTTPException(409,"canonical resume page count must be verified before fact import")
            page_count=resume["page_count"]
            from .storage import encrypt_document
            key=_document_key(); inserted=0
            for item in payload.facts:
                try:
                    fact=validate_resume_fact(ResumeFact(item.text,item.page,item.section,item.confidence,item.sensitivity),page_count)
                except ValueError as exc:
                    raise HTTPException(422,str(exc)) from None
                fact_id=uuid4(); status=FactStatus.NEEDS_REVIEW
                # Straightforward verbatim lines may be marked as resume evidence;
                # this is explicitly distinct from owner approval.
                if fact.confidence >= .95 and fact.sensitivity == "NORMAL":
                    status=FactStatus.RESUME_VERIFIED
                provenance={"source":"canonical-resume","source_sha256":payload.source_sha256,
                            "source_revision":payload.source_revision,"page":fact.page,
                            "section":fact.section,"ordinal":item.ordinal,
                            "meaning":"document evidence only; not owner approval"}
                result=conn.execute("""INSERT INTO candidate_fact(id,profile_id,resume_id,fact_ciphertext,
                  source_sha256,source_kind,source_ordinal,page_number,section,confidence,sensitivity,status,provenance)
                  VALUES (%s,%s,%s,%s,%s,'CANONICAL_RESUME',%s,%s,%s,%s,%s,%s,%s)
                  ON CONFLICT (resume_id,source_sha256,source_ordinal) WHERE source_kind='CANONICAL_RESUME'
                  DO NOTHING""",(fact_id,resume["profile_id"],resume_id,
                  encrypt_document(fact.text.encode(),key,fact_id),payload.source_sha256,item.ordinal,
                  fact.page,fact.section,fact.confidence,fact.sensitivity,status.value,Jsonb(provenance)))
                inserted += result.rowcount
            conn.execute("INSERT INTO audit_event(id,actor,action,object_type,object_id,request_id,result,metadata) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                         (uuid4(),principal.email,"CANONICAL_RESUME_FACTS_IMPORTED","resume",resume_id,uuid4(),
                          f"{inserted} inserted",Jsonb({"source_sha256":payload.source_sha256,"batch_size":len(payload.facts)})))
    return {"resume_id":str(resume_id),"received":len(payload.facts),"inserted":inserted,
            "duplicates_skipped":len(payload.facts)-inserted,"status":"REVIEW WORKFLOW"}


@app.get("/api/v1/profile/facts")
async def list_candidate_facts(status: FactStatus | None = None, section: str | None = None,
                               sensitivity: str | None = None, min_confidence: float | None = None,
                               limit: int = 100, offset: int = 0,
                               _: Principal = Depends(_owner)):
    if config.DEMO_MODE:
        return {"mode":"DEMO","items":[],"total":0}
    if sensitivity and sensitivity not in {"NORMAL","SENSITIVE","LEGAL"}:
        raise HTTPException(422,"invalid sensitivity filter")
    if not 1 <= limit <= 200 or offset < 0 or (min_confidence is not None and not 0 <= min_confidence <= 1):
        raise HTTPException(422,"invalid fact filter")
    from .storage import decrypt_document
    clauses=[]; params=[]
    for column,value in (("status",status.value if status else None),("section",section.upper() if section else None),
                         ("sensitivity",sensitivity),("confidence",min_confidence)):
        if value is not None:
            clauses.append(f"{column} >= %s" if column=="confidence" else f"{column}=%s"); params.append(value)
    where=(" WHERE "+" AND ".join(clauses)) if clauses else ""
    with db.connection() as conn:
        rows=conn.execute("""SELECT id, fact_ciphertext, status, section, confidence, sensitivity,
          source_kind,source_sha256,page_number,provenance,reviewed_at,reviewed_by
          FROM candidate_fact"""+where+" ORDER BY section,page_number,created_at LIMIT %s OFFSET %s",
          (*params,limit,offset)).fetchall()
        total=conn.execute("SELECT count(*) FROM candidate_fact"+where,params).fetchone()[0]
    key=_document_key(); items=[]
    for row in rows:
        try: text=decrypt_document(row["fact_ciphertext"],key,row["id"]).decode()
        except Exception: raise HTTPException(503,"candidate fact decryption failed") from None
        items.append({"id":str(row["id"]),"text":text,"status":row["status"],"section":row["section"],
          "confidence":float(row["confidence"]),"sensitivity":row["sensitivity"],"source":row["source_kind"],
          "source_sha256":row["source_sha256"].strip() if row["source_sha256"] else None,
          "page":row["page_number"],"provenance":row["provenance"],"reviewed_at":row["reviewed_at"],
          "reviewed_by":row["reviewed_by"]})
    return {"items":items,"total":total,"limit":limit,"offset":offset}


class FactReview(BaseModel):
    fact_ids: list[UUID] = Field(min_length=1,max_length=100)
    status: FactStatus


class FactEdit(BaseModel):
    text: str = Field(min_length=1,max_length=5000)


@app.post("/api/v1/profile/facts/review")
async def review_candidate_facts(payload: FactReview, principal: Principal = Depends(_owner)):
    if config.DEMO_MODE:
        return {"mode":"DEMO","changed":0}
    if len(set(payload.fact_ids)) != len(payload.fact_ids):
        raise HTTPException(422,"fact IDs must be unique")
    with db.connection() as conn:
        with conn.transaction():
            rows=conn.execute("""SELECT id,status,source_kind,confidence,sensitivity FROM candidate_fact
              WHERE id=ANY(%s) FOR UPDATE""",(payload.fact_ids,)).fetchall()
            if len(rows) != len(payload.fact_ids):
                raise HTTPException(404,"one or more candidate facts were not found")
            for row in rows:
                if not can_bulk_transition(FactStatus(row["status"]),payload.status,
                    canonical=row["source_kind"]=="CANONICAL_RESUME",confidence=float(row["confidence"]),
                    sensitivity=row["sensitivity"]):
                    raise HTTPException(409,"requested status is not valid for every selected fact")
            conn.execute("UPDATE candidate_fact SET status=%s,reviewed_by=%s,reviewed_at=now(),updated_at=now() WHERE id=ANY(%s)",
                         (payload.status.value,principal.email,payload.fact_ids))
            conn.execute("INSERT INTO audit_event(id,actor,action,object_type,request_id,result,metadata) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                         (uuid4(),principal.email,"CANDIDATE_FACTS_REVIEWED","candidate_fact",uuid4(),payload.status.value,
                          Jsonb({"count":len(rows)})))
    return {"changed":len(rows),"status":payload.status.value}


@app.put("/api/v1/profile/facts/{fact_id}")
async def edit_candidate_fact(fact_id: UUID, payload: FactEdit, principal: Principal = Depends(_owner)):
    if config.DEMO_MODE:
        raise HTTPException(409,"demo mode does not accept personal facts")
    enforce_owner_limit(principal,"candidate-fact-edit",100,3600)
    key=_document_key()
    from .storage import encrypt_document
    with db.connection() as conn:
        with conn.transaction():
            row=conn.execute("SELECT provenance,sensitivity FROM candidate_fact WHERE id=%s FOR UPDATE",(fact_id,)).fetchone()
            if not row: raise HTTPException(404,"candidate fact not found")
            provenance=dict(row["provenance"] or {})
            provenance["previous_source_kind"]=provenance.get("source_kind","canonical-resume")
            provenance["owner_edited"]=True
            provenance["owner_editor"]="owner"
            from .domain.resume_extract import CONTACT_TEXT,SENSITIVE_TEXT
            inferred="LEGAL" if SENSITIVE_TEXT.search(payload.text) else "SENSITIVE" if CONTACT_TEXT.search(payload.text) else "NORMAL"
            rank={"NORMAL":0,"SENSITIVE":1,"LEGAL":2}
            sensitivity=max((row["sensitivity"],inferred),key=rank.__getitem__)
            conn.execute("""UPDATE candidate_fact SET fact_ciphertext=%s,source_kind='OWNER_INPUT',
              sensitivity=%s,status='NEEDS_REVIEW',reviewed_by=NULL,reviewed_at=NULL,provenance=%s,updated_at=now()
              WHERE id=%s""",(encrypt_document(payload.text.strip().encode(),key,fact_id),sensitivity,Jsonb(provenance),fact_id))
            conn.execute("INSERT INTO audit_event(id,actor,action,object_type,object_id,request_id,result) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                         (uuid4(),principal.email,"CANDIDATE_FACT_EDITED","candidate_fact",fact_id,uuid4(),"NEEDS_REVIEW"))
    return {"id":str(fact_id),"status":"NEEDS_REVIEW"}


class JobImport(BaseModel):
    company: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=8, max_length=2000)
    source: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=120000)
    location: str = Field(default="Unspecified", max_length=300)
    province: str | None = None
    work_mode: str | None = None
    external_id: str | None = None


class PublicATSConfiguration(BaseModel):
    provider: Literal["greenhouse", "lever"]
    board_slug: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    company_name: str | None = Field(default=None, min_length=1, max_length=120)
    terms_reviewed: bool
    enabled: bool = False


@app.put("/api/v1/integrations/public-ats")
async def configure_public_ats(payload: PublicATSConfiguration, principal: Principal = Depends(_owner)):
    """Register one fixed-host public ATS board after an explicit owner terms review."""
    if config.DEMO_MODE:
        return {"mode":"DEMO","saved":False}
    if not payload.terms_reviewed:
        raise HTTPException(409,"review the source terms before enabling this public board")
    from .providers.public_ats import PublicBoard
    host={"greenhouse":"boards-api.greenhouse.io","lever":"api.lever.co"}[payload.provider]
    try:
        PublicBoard(payload.provider,host,payload.board_slug).url()
    except ValueError:
        raise HTTPException(422,"public ATS source configuration is invalid") from None
    enforce_owner_limit(principal,"public-ats-config",10,3600)
    source_name=f"{payload.provider}:{payload.board_slug.casefold()}"
    identity=uuid4()
    with db.connection() as conn:
        with conn.transaction():
            row=conn.execute("""INSERT INTO job_source(id,name,source_type,permitted,terms_reviewed_at,enabled,configuration)
              VALUES (%s,%s,'API',true,now(),%s,%s)
              ON CONFLICT(name) DO UPDATE SET source_type='API',permitted=true,terms_reviewed_at=now(),
                enabled=excluded.enabled,configuration=excluded.configuration RETURNING id,enabled""",
              (identity,source_name,payload.enabled,Jsonb({"provider":payload.provider,"board_slug":payload.board_slug,
                "company_name":payload.company_name}))).fetchone()
            conn.execute("""INSERT INTO audit_event(id,actor,action,object_type,object_id,request_id,result,metadata)
              VALUES (%s,%s,'PUBLIC_ATS_SOURCE_CONFIGURED','job_source',%s,%s,%s,%s)""",
              (uuid4(),principal.email,row["id"],uuid4(),"ENABLED" if row["enabled"] else "DISABLED",
               Jsonb({"provider":payload.provider,"terms_reviewed":True})))
    return {"source":"public-ats","provider":payload.provider,"enabled":row["enabled"],
            "terms_reviewed":True,"external_requests":False if not row["enabled"] else "scheduled"}


SENSITIVE_FACTS = {"work_authorization", "immigration_status", "disability", "medical", "race", "ethnicity",
                   "religion", "gender_identity", "sexual_orientation", "veteran_status", "criminal_history",
                   "security_clearance", "salary_commitment"}


def _is_sensitive_fact(key: str) -> bool:
    normalized=key.casefold()
    sensitive_terms=("work_author", "immigra", "disab", "medical", "race", "ethnic", "relig", "gender",
                     "sexual", "veteran", "criminal", "security_clearance", "salary")
    return normalized in SENSITIVE_FACTS or any(term in normalized for term in sensitive_terms)


class ProfileInput(BaseModel):
    facts: dict[str, str] = Field(max_length=80)


def _document_key() -> bytes:
    path = os.getenv("DOCUMENT_ENCRYPTION_KEY_FILE")
    if not path:
        raise HTTPException(503, "profile encryption unavailable")
    try:
        return load_key_file(path)
    except Exception:
        raise HTTPException(503, "profile encryption unavailable") from None


def _profile_rows(profile_id: UUID, facts: dict[str, str], actor: str, key: bytes, conn) -> None:
    profile_payload = json.dumps(facts, sort_keys=True, ensure_ascii=False).encode()
    from .storage import encrypt_document
    conn.execute("INSERT INTO candidate_profile(id,facts_ciphertext) VALUES (%s,%s)",
                 (profile_id, encrypt_document(profile_payload,key,profile_id)))
    for name,value in facts.items():
        if not name.replace("_", "").isalnum() or len(name)>100:
            raise HTTPException(422,"invalid profile fact key")
        fact_id=uuid4()
        sensitivity="SENSITIVE" if _is_sensitive_fact(name) else "NORMAL"
        conn.execute("""INSERT INTO profile_fact(id,profile_id,fact_key,value_ciphertext,verified,source,sensitivity)
          VALUES (%s,%s,%s,%s,false,%s,%s)""",
            (fact_id,profile_id,name,encrypt_document(value.encode(),key,fact_id),"owner-entered; review required",sensitivity))
    conn.execute("INSERT INTO audit_event(id,actor,action,object_type,object_id,request_id,result) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                 (uuid4(),actor,"PROFILE_CREATED","candidate_profile",profile_id,uuid4(),"FACTS_PENDING_REVIEW"))


@app.get("/api/v1/profile")
async def get_profile(principal: Principal = Depends(_owner)):
    if config.DEMO_MODE:
        return {"mode":"DEMO","facts":{},"verification":"SYNTHETIC"}
    from .storage import decrypt_document
    key=_document_key()
    with db.connection() as conn:
        profile=conn.execute("SELECT id,facts_ciphertext,created_at,updated_at FROM candidate_profile ORDER BY created_at LIMIT 1").fetchone()
        if not profile:
            return {"configured":False,"facts":{},"verified_count":0,"pending_count":0,"fact_status":{}}
        facts=json.loads(decrypt_document(profile["facts_ciphertext"],key,profile["id"]))
        statuses=conn.execute("SELECT fact_key,verified,sensitivity FROM profile_fact WHERE profile_id=%s",(profile["id"],)).fetchall()
    counts={False:0,True:0}
    fact_status={row["fact_key"]:{"verified":row["verified"],"sensitivity":row["sensitivity"]} for row in statuses}
    for item in statuses: counts[bool(item["verified"])]+=1
    return {"configured":True,"facts":facts,"verified_count":counts[True],
            "pending_count":counts[False],"fact_status":fact_status}


@app.put("/api/v1/profile")
async def create_profile(payload: ProfileInput, principal: Principal = Depends(_owner)):
    if config.DEMO_MODE:
        return {"mode":"DEMO","saved":False,"reason":"demo mode never stores profile data"}
    enforce_owner_limit(principal,"profile-update",5,3600)
    try:
        incoming={key:value for key,value in validate_profile_facts(payload.facts).items() if value}
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from None
    profile_id=uuid4();key=_document_key()
    with db.connection() as conn:
        with conn.transaction():
            exists=conn.execute("SELECT 1 FROM candidate_profile LIMIT 1").fetchone()
            if exists:
                raise HTTPException(409,"profile already exists; use a reviewed update workflow")
            _profile_rows(profile_id,incoming,principal.email,key,conn)
    return {"saved":True,"profile_id":str(profile_id),"facts_pending_review":len(incoming)}


@app.patch("/api/v1/profile")
async def update_profile(payload: ProfileInput, principal: Principal = Depends(_owner)):
    if config.DEMO_MODE:
        return {"mode":"DEMO","saved":False,"reason":"demo mode never stores profile data"}
    enforce_owner_limit(principal,"profile-update",10,3600)
    try:
        incoming=validate_profile_facts(payload.facts)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from None
    from .storage import decrypt_document, encrypt_document
    key=_document_key()
    with db.connection() as conn:
        with conn.transaction():
            profile=conn.execute("SELECT id,facts_ciphertext FROM candidate_profile ORDER BY created_at LIMIT 1 FOR UPDATE").fetchone()
            if not profile:
                raise HTTPException(409,"profile setup is required")
            current=json.loads(decrypt_document(profile["facts_ciphertext"],key,profile["id"]))
            merged=merge_profile_facts(current,incoming)
            conn.execute("UPDATE candidate_profile SET facts_ciphertext=%s,updated_at=now() WHERE id=%s",
                         (encrypt_document(json.dumps(merged,sort_keys=True,ensure_ascii=False).encode(),key,profile["id"]),profile["id"]))
            for name,value in incoming.items():
                if not value:
                    conn.execute("DELETE FROM profile_fact WHERE profile_id=%s AND fact_key=%s",
                                 (profile["id"],name))
                    continue
                fact=conn.execute("SELECT id FROM profile_fact WHERE profile_id=%s AND fact_key=%s FOR UPDATE",
                                  (profile["id"],name)).fetchone()
                sensitivity="SENSITIVE" if _is_sensitive_fact(name) else "NORMAL"
                if fact:
                    conn.execute("""UPDATE profile_fact SET value_ciphertext=%s,verified=false,source='owner-edited; review required',
                      approved_at=NULL,sensitivity=%s WHERE id=%s""",
                      (encrypt_document(value.encode(),key,fact["id"]),sensitivity,fact["id"]))
                else:
                    fact_id=uuid4()
                    conn.execute("""INSERT INTO profile_fact(id,profile_id,fact_key,value_ciphertext,verified,source,sensitivity)
                      VALUES (%s,%s,%s,%s,false,'owner-entered; review required',%s)""",
                      (fact_id,profile["id"],name,encrypt_document(value.encode(),key,fact_id),sensitivity))
            for name in current.keys()-merged.keys():
                conn.execute("DELETE FROM profile_fact WHERE profile_id=%s AND fact_key=%s",
                             (profile["id"],name))
            conn.execute("INSERT INTO audit_event(id,actor,action,object_type,object_id,request_id,result) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                         (uuid4(),principal.email,"PROFILE_UPDATED","candidate_profile",profile["id"],uuid4(),"FACTS_PENDING_REVIEW"))
    return {"saved":True,"profile_id":str(profile["id"]),"facts_pending_review":len(incoming)}


@app.get("/api/v1/audit")
async def audit_events(_: Principal = Depends(_owner)):
    if config.DEMO_MODE:
        return {"mode":"DEMO","items":[
            {"action":"JOB_DISCOVERED","object_type":"job","result":"SYNTHETIC","created_at":"2026-10-29T13:42:00-04:00"},
            {"action":"DUPLICATE_CHECKED","object_type":"job","result":"SYNTHETIC","created_at":"2026-10-29T13:42:03-04:00"},
        ]}
    with db.connection() as conn:
        rows=conn.execute("""SELECT action,object_type,result,created_at FROM audit_event
          ORDER BY created_at DESC LIMIT 100""").fetchall()
    return {"items":[{"action":r["action"],"object_type":r["object_type"],
                       "result":r["result"],"created_at":r["created_at"]} for r in rows]}


@app.post("/api/v1/profile/facts/{fact_key}/verify")
async def verify_profile_fact(fact_key: str, principal: Principal = Depends(_owner)):
    if config.DEMO_MODE:
        return {"mode":"DEMO","changed":False}
    if _is_sensitive_fact(fact_key):
        raise HTTPException(409,"sensitive or legal answers require the approved-answer workflow")
    with db.connection() as conn:
        with conn.transaction():
            result=conn.execute("UPDATE profile_fact SET verified=true,approved_at=now() WHERE fact_key=%s AND sensitivity='NORMAL' AND verified=false RETURNING id,profile_id",(fact_key,)).fetchone()
            if not result: raise HTTPException(404,"unverified fact not found")
            conn.execute("INSERT INTO audit_event(id,actor,action,object_type,object_id,request_id,result) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                         (uuid4(),principal.email,"PROFILE_FACT_VERIFIED","profile_fact",result["id"],uuid4(),"APPROVED"))
    return {"verified":True,"fact_key":fact_key}


@app.post("/api/v1/jobs/import", status_code=202)
async def import_job(payload: JobImport, principal: Principal = Depends(_owner)):
    if config.DEMO_MODE:
        return {"accepted":False,"mode":"DEMO","reason":"demo mode does not persist owner or external data"}
    enforce_owner_limit(principal,"job-import",20,3600)
    job = normalize_job(payload.model_dump())
    job_id = uuid4()
    with db.connection() as conn:
        with conn.transaction():
            source = conn.execute("SELECT id FROM job_source WHERE name=%s AND enabled AND permitted", (job.source,)).fetchone()
            if not source:
                raise HTTPException(409, "source is not enabled and terms-reviewed")
            existing = conn.execute("SELECT id::text FROM job WHERE posting_fingerprint=%s OR canonical_url=%s LIMIT 1",
                                    (job.fingerprint,job.canonical_url)).fetchone()
            if existing:
                return {"accepted":True,"duplicate":True,"job_id":existing["id"]}
            conn.execute("""INSERT INTO job (id,source_id,company,title,description,city,province,work_mode,
              canonical_url,external_id,posting_fingerprint,description_fingerprint,source_evidence)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
              (job_id,source["id"],job.company,job.title,job.description,job.location,job.province,
               job.work_mode,job.canonical_url,job.external_id,job.fingerprint,
               hashlib.sha256(job.description.encode()).hexdigest(),Jsonb({"source":job.source,"untrusted":True})))
            description_hash=hashlib.sha256(job.description.encode("utf-8")).hexdigest()
            conn.execute("INSERT INTO job_snapshot(id,job_id,content_hash,content) VALUES (%s,%s,%s,%s)",
              (uuid4(),job_id,description_hash,Jsonb({"company":job.company,"title":job.title,
               "location":job.location,"description":job.description,"canonical_url":job.canonical_url,
               "source":job.source,"untrusted":True})))
            from .tasks import _score_new_job
            _score_new_job(conn,job_id)
            conn.execute("INSERT INTO audit_event(id,actor,action,object_type,object_id,request_id,result) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                         (uuid4(),principal.email,"JOB_IMPORTED","job",job_id,uuid4(),"ACCEPTED"))
    return {"accepted":True,"duplicate":False,"job_id":str(job_id)}


@app.post("/api/v1/agent/emergency-stop")
async def emergency_stop(principal: Principal = Depends(_owner)):
    if config.DEMO_MODE:
        return {"mode":"DEMO","changed":False,"reason":"production controls are unavailable in demo"}
    with db.connection() as conn:
        with conn.transaction():
            conn.execute("UPDATE agent_configuration SET emergency_stopped=true, paused=true, updated_by=%s, updated_at=now() WHERE agent='submission'",
                         (principal.email,))
            conn.execute("INSERT INTO audit_event(id,actor,action,object_type,request_id,result) VALUES (%s,%s,%s,%s,%s,%s)",
                         (uuid4(),principal.email,"EMERGENCY_STOP","agent_configuration",uuid4(),"APPLIED"))
    return {"emergency_stopped":True,"new_submissions":"BLOCKED"}


@app.post("/api/v1/agent/control")
async def control_agent(payload: dict, principal: Principal = Depends(_owner)):
    allowed = {"discovery", "matching", "documents", "submission", "sheet_sync", "email", "followups"}
    agent, action = payload.get("agent"), payload.get("action")
    if agent not in allowed or action not in {"START", "PAUSE", "DRAIN", "STOP", "EMERGENCY_STOP"}:
        raise HTTPException(422, "invalid agent control command")
    if agent == "submission" and action == "START" and not config.AUTONOMOUS_SUBMISSION_ENABLED:
        raise HTTPException(409, "submission remains disabled pending owner activation gate")
    if action == "START" and agent not in RUNNABLE_WORKERS:
        raise HTTPException(409, "no runnable handler is configured for this worker")
    if config.DEMO_MODE:
        return {"mode":"DEMO","changed":False,"reason":"demo controls never start workers"}
    if action == "EMERGENCY_STOP":
        return await emergency_stop(principal)
    enforce_owner_limit(principal,"agent-control",120,3600)
    if agent == "submission" and action == "START":
        with db.connection() as conn:
            state=conn.execute("SELECT emergency_stopped FROM agent_configuration WHERE agent='submission'").fetchone()
        if not state or state["emergency_stopped"]:
            raise HTTPException(409,"submission emergency stop must be cleared through the attended activation workflow")
    updates={"START":(True,False,False),"PAUSE":(True,True,False),"DRAIN":(True,True,True),"STOP":(False,True,False)}[action]
    with db.connection() as conn:
        with conn.transaction():
            row=conn.execute("""UPDATE agent_configuration SET enabled=%s,paused=%s,drain=%s,updated_by=%s,updated_at=now()
              WHERE agent=%s RETURNING enabled,paused,drain""",(*updates,principal.email,agent)).fetchone()
            if not row: raise HTTPException(503,"agent control state is unavailable")
            conn.execute("INSERT INTO audit_event(id,actor,action,object_type,request_id,result) VALUES (%s,%s,%s,%s,%s,%s)",
                         (uuid4(),principal.email,"AGENT_CONTROL_"+action,"agent_configuration",uuid4(),agent))
    return {"agent":agent,"enabled":row["enabled"],"paused":row["paused"],"drain":row["drain"]}


@app.post("/api/v1/documents/resumes", status_code=201)
async def upload_resume(request: Request, file: UploadFile, principal: Principal = Depends(_owner)):
    if config.DEMO_MODE:
        raise HTTPException(409, "demo mode does not accept personal documents")
    enforce_owner_limit(principal,"resume-upload",5,3600)
    length = request.headers.get("content-length")
    if length:
        try:
            if int(length) < 0 or int(length) > MAX_UPLOAD_BYTES + 65536:
                raise HTTPException(413, "document exceeds upload limit")
        except ValueError:
            raise HTTPException(400, "invalid content length") from None
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    try:
        validate_pdf(file.filename or "", file.content_type or "", data)
        from .domain.resume_extract import extract_resume
        page_count, extracted_facts = extract_resume(data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except Exception:
        # Parser diagnostics can contain document metadata and are intentionally withheld.
        raise HTTPException(422, "resume PDF could not be safely parsed") from None
    display_name = safe_display_name(file.filename or "resume.pdf")
    key_file = os.getenv("DOCUMENT_ENCRYPTION_KEY_FILE")
    if not key_file:
        raise HTTPException(503, "document encryption unavailable")
    key = load_key_file(key_file)
    # A blank private profile can hold the initial resume until the owner completes
    # profile setup. No candidate values are inferred into the profile here.
    with db.connection() as conn:
        profile = conn.execute("SELECT id FROM candidate_profile ORDER BY created_at LIMIT 1").fetchone()
        if not profile:
            profile_id=uuid4()
            from .storage import encrypt_document
            with conn.transaction():
                conn.execute("INSERT INTO candidate_profile(id,facts_ciphertext) VALUES (%s,%s)",
                             (profile_id,encrypt_document(b"{}",key,profile_id)))
            profile={"id":profile_id}
        has_canonical = conn.execute("SELECT 1 FROM resume WHERE profile_id=%s AND canonical AND active LIMIT 1",
                                     (profile["id"],)).fetchone() is not None
        imported_at = datetime.now(timezone.utc)
        doc_id, storage_key, sha256 = store_private(data, display_name, config.DOCUMENT_ROOT, key)
        try:
          with conn.transaction():
            conn.execute("""INSERT INTO resume(id,profile_id,version,label,category,sha256,mime_type,size_bytes,
              storage_key,canonical,active,imported_at,provenance,page_count) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
              (doc_id,profile["id"],"resume-upload-"+doc_id.hex[:8],display_name,"Uncategorized",sha256,
               "application/pdf",len(data),storage_key,not has_canonical,not has_canonical,imported_at,
               Jsonb({"imported_by":principal.email,"source":"owner-upload"}),page_count))
            conn.execute("""INSERT INTO document(id,profile_id,document_type,sha256,mime_type,size_bytes,
              storage_key,encrypted,provenance) VALUES (%s,%s,%s,%s,%s,%s,%s,true,%s)""",
              (doc_id,profile["id"],"CANONICAL_RESUME" if not has_canonical else "OTHER",sha256,"application/pdf",len(data),storage_key,
               Jsonb({"filename":display_name,"imported_by":principal.email})))
            inserted_facts=0
            if not has_canonical:
                from .domain.resume_extract import ExtractedFact
                for item in extracted_facts:
                    fact_id=uuid4()
                    status="RESUME_VERIFIED" if item.sensitivity=="NORMAL" else "NEEDS_REVIEW"
                    provenance={"source":"canonical-resume","sha256":sha256,"page":item.page,
                                "section":item.section,"ordinal":item.ordinal,
                                "meaning":"document evidence only; not owner approval"}
                    conn.execute("""INSERT INTO candidate_fact(id,profile_id,resume_id,fact_ciphertext,
                      source_sha256,source_kind,source_ordinal,page_number,section,confidence,sensitivity,status,provenance)
                      VALUES (%s,%s,%s,%s,%s,'CANONICAL_RESUME',%s,%s,%s,%s,%s,%s,%s)""",
                      (fact_id,profile["id"],doc_id,encrypt_document(item.text.encode(),key,fact_id),sha256,
                       item.ordinal,item.page,item.section,item.confidence,item.sensitivity,status,Jsonb(provenance)))
                    inserted_facts+=1
            else:
                inserted_facts=0
        except Exception:
            # The file is encrypted and private; remove it only if the metadata
            # transaction failed so an unreferenced copy is not left behind.
            path = config.DOCUMENT_ROOT / storage_key
            try:
                path.unlink()
            except OSError:
                pass
            raise
    return {"id":str(doc_id),"filename":display_name,"mime_type":"application/pdf","size_bytes":len(data),
            "sha256":sha256,"canonical":not has_canonical,"active":not has_canonical,
            "page_count":page_count,"facts_extracted":inserted_facts,"imported_at":imported_at.isoformat(),
            "audit":"Document imported; content excluded from logs"}


class DocumentGenerate(BaseModel):
    kind: Literal["ATS_RESUME","COVER_LETTER","SCREENING_PACKAGE","APPLICATION_SUMMARY"]
    variant: Literal["A","B","C","D"] | None = None
    job_id: UUID | None = None
    application_id: UUID | None = None


@app.post("/api/v1/documents/generate",status_code=201)
async def generate_private_document(payload: DocumentGenerate, principal: Principal = Depends(_owner)):
    if config.DEMO_MODE:
        raise HTTPException(409,"demo mode does not generate personal documents")
    if payload.job_id and payload.application_id:
        raise HTTPException(422,"provide a job or application reference, not both")
    if (payload.kind=="ATS_RESUME") != (payload.variant is not None):
        raise HTTPException(422,"ATS resumes require one of the four defined variants")
    enforce_owner_limit(principal,"document-generate",20,3600)
    from .domain.document_generation import DocumentKind, generate_document
    from .storage import store_private_blob
    key=_document_key()
    with db.connection() as conn:
        profile=conn.execute("SELECT id FROM candidate_profile ORDER BY created_at LIMIT 1").fetchone()
        if not profile:
            raise HTTPException(409,"candidate profile setup is required")
        resume=conn.execute("SELECT id FROM resume WHERE profile_id=%s AND canonical AND active LIMIT 1",
                            (profile["id"],)).fetchone()
        if not resume:
            raise HTTPException(409,"an active canonical resume is required")
        job=None;application_id=payload.application_id
        if application_id:
            job=conn.execute("""SELECT j.id,j.title,j.company FROM application a JOIN job j ON j.id=a.job_id
              WHERE a.id=%s AND a.profile_id=%s""",(application_id,profile["id"])).fetchone()
            if not job: raise HTTPException(404,"application not found")
        elif payload.job_id:
            job=conn.execute("SELECT id,title,company FROM job WHERE id=%s",(payload.job_id,)).fetchone()
            if not job: raise HTTPException(404,"job not found")
        facts=conn.execute("""SELECT id,fact_ciphertext,status,page_number,section,source_kind
          FROM candidate_fact WHERE profile_id=%s ORDER BY section,page_number,source_ordinal LIMIT 500""",
          (profile["id"],)).fetchall()
    from .storage import decrypt_document
    source_facts=[]
    for fact in facts:
        try: text=decrypt_document(fact["fact_ciphertext"],key,fact["id"]).decode("utf-8")
        except Exception: raise HTTPException(503,"candidate evidence could not be decrypted") from None
        source_facts.append({"id":str(fact["id"]),"text":text,"status":fact["status"],
          "page":fact["page_number"],"section":fact["section"],"source":fact["source_kind"]})
    variants={
      "A":("Systems / Infrastructure",("linux","windows server","docker","proxmox","zfs","infrastructure","server","monitoring","backup","terraform","network")),
      "B":("L2 / Desktop / Technical",("windows","endpoint","desktop","technical support","troubleshooting","incident","customer support","microsoft 365")),
      "C":("Help Desk / L1",("help desk","service desk","technical support","troubleshooting","customer service","incident resolution")),
      "D":("Cloud",("azure","aws","cloudflare","terraform","cloud","vnet","cloud computing")),
    }
    selected_facts=source_facts
    if payload.variant:
        _,keywords=variants[payload.variant]
        selected_facts=[fact for fact in source_facts if any(word in fact["text"].casefold() for word in keywords)]
        if not selected_facts:
            raise HTTPException(409,"no verified canonical facts match this resume variant")
    try:
        generated=generate_document(DocumentKind(payload.kind),selected_facts,resume_active=True,
          job_title=job["title"] if job else "",company=job["company"] if job else "")
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from None
    data=generated.text.encode("utf-8")
    identity,storage_key,sha256=store_private_blob(data,"txt",config.DOCUMENT_ROOT,key)
    doc_type={"ATS_RESUME":"TAILORED_RESUME","COVER_LETTER":"COVER_LETTER",
              "SCREENING_PACKAGE":"SCREENING_RESPONSE","APPLICATION_SUMMARY":"APPLICATION_SUMMARY"}[payload.kind]
    provenance={"generator":"verified-fact-template-v1","source_fact_ids":[str(x["fact_id"]) for x in generated.provenance],
      "variant":variants[payload.variant][0] if payload.variant else None,
      "resume_base_id":str(resume["id"]),"job_id":str(job["id"]) if job else None,
      "application_id":str(application_id) if application_id else None}
    try:
        with db.connection() as conn:
            with conn.transaction():
                conn.execute("""INSERT INTO document(id,profile_id,application_id,job_id,document_type,sha256,mime_type,
                  size_bytes,storage_key,encrypted,resume_base_id,provenance)
                  VALUES (%s,%s,%s,%s,%s,%s,'text/plain',%s,%s,true,%s,%s)""",
                  (identity,profile["id"],application_id,job["id"] if job else None,doc_type,sha256,
                   len(data),storage_key,resume["id"],Jsonb(provenance)))
                conn.execute("""INSERT INTO audit_event(id,actor,action,object_type,object_id,request_id,result,metadata)
                  VALUES (%s,%s,'PRIVATE_DOCUMENT_GENERATED','document',%s,%s,%s,%s)""",
                  (uuid4(),principal.email,identity,uuid4(),doc_type,Jsonb({"sha256":sha256,"claim_count":len(generated.provenance)})))
    except Exception:
        try: (config.DOCUMENT_ROOT/storage_key).unlink()
        except OSError: pass
        raise
    return {"id":str(identity),"document_type":doc_type,"mime_type":"text/plain","size_bytes":len(data),
      "sha256":sha256,"claim_count":len(generated.provenance),"provenance":generated.provenance,
      "download_url":f"/api/v1/documents/{identity}/download"}


@app.get("/api/v1/documents")
async def list_private_documents(_: Principal = Depends(_owner)):
    if config.DEMO_MODE: return {"mode":"DEMO","items":[]}
    with db.connection() as conn:
        rows=conn.execute("""SELECT id::text,document_type,sha256,mime_type,size_bytes,created_at,
          application_id::text,job_id::text,resume_base_id::text,provenance FROM document
          WHERE document_type<>'CANONICAL_RESUME' ORDER BY created_at DESC LIMIT 200""").fetchall()
    return {"items":[{**row,"download_url":f"/api/v1/documents/{row['id']}/download"} for row in rows]}


@app.get("/api/v1/documents/{document_id}/download")
async def download_private_document(document_id: UUID, principal: Principal = Depends(_owner)):
    if config.DEMO_MODE: raise HTTPException(404,"document not found")
    from .storage import decrypt_document
    with db.connection() as conn:
        row=conn.execute("SELECT sha256,mime_type,size_bytes,storage_key FROM document WHERE id=%s AND encrypted",
                         (document_id,)).fetchone()
    if not row or row["mime_type"]!="text/plain": raise HTTPException(404,"document not found")
    if not re.fullmatch(r"[0-9a-f]{2}/[0-9a-f]{32}\.txt\.enc",row["storage_key"]):
        raise HTTPException(500,"document metadata is invalid")
    path=config.DOCUMENT_ROOT/row["storage_key"]
    try:
        if path.is_symlink() or not path.is_file(): raise OSError("unavailable")
        data=decrypt_document(path.read_bytes(),_document_key(),document_id)
    except Exception: raise HTTPException(500,"document could not be opened") from None
    if len(data)!=row["size_bytes"] or hashlib.sha256(data).hexdigest()!=row["sha256"]:
        raise HTTPException(500,"document integrity check failed")
    return Response(data,media_type="text/plain; charset=utf-8",headers={
      "Content-Disposition":"attachment; filename=bruteapply-document.txt","Cache-Control":"private, no-store",
      "X-Content-Type-Options":"nosniff"})


@app.get("/api/v1/resumes/{resume_id}/download")
async def download_resume(resume_id: UUID, principal: Principal = Depends(_owner)):
    if config.DEMO_MODE:
        raise HTTPException(404,"resume not found")
    from .storage import decrypt_document
    with db.connection() as conn:
        row=conn.execute("SELECT sha256,mime_type,label,storage_key FROM resume WHERE id=%s",(resume_id,)).fetchone()
    if not row: raise HTTPException(404,"resume not found")
    storage_key=row["storage_key"]
    if not re.fullmatch(r"[0-9a-f]{2}/[0-9a-f]{32}\.pdf\.enc",storage_key):
        raise HTTPException(500,"document metadata is invalid")
    path=config.DOCUMENT_ROOT/storage_key
    try:
        if path.is_symlink() or not path.is_file(): raise OSError("document unavailable")
        data=decrypt_document(path.read_bytes(),_document_key(),resume_id)
    except Exception:
        raise HTTPException(500,"document could not be opened") from None
    if hashlib.sha256(data).hexdigest()!=row["sha256"]:
        raise HTTPException(500,"document integrity check failed")
    try:
        validate_pdf(row["label"],row["mime_type"],data)
    except ValueError:
        raise HTTPException(500,"stored document failed validation") from None
    return Response(data,media_type="application/pdf",headers={"Content-Disposition":"attachment; filename=resume.pdf",
        "Cache-Control":"private, no-store","X-Content-Type-Options":"nosniff"})
