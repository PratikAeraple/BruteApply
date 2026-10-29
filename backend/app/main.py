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
    return {"status": "ok", "application": "bruteapply", "mode": "DEMO" if config.DEMO_MODE else "PRODUCTION"}


@app.get("/readyz")
async def readyz():
    if config.DEMO_MODE:
        return {"status": "ready", "application": "bruteapply-demo"}
    if not db.ready():
        raise HTTPException(503, "not ready")
    return {"status": "ready", "application": "bruteapply"}


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
    result["workers"]={r["agent"]:("EMERGENCY STOP" if r["emergency_stopped"] else "PAUSED" if r["paused"]
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
          m.score,m.confidence,m.matched,m.missing,m.hard_blockers,m.recommended_action,
          j.canonical_url,j.discovered_at FROM job j LEFT JOIN job_match m ON m.job_id=j.id
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
            current = conn.execute("SELECT state FROM ticket WHERE id=%s FOR UPDATE",(ticket_id,)).fetchone()
            if not current:
                raise HTTPException(404,"ticket not found")
            try:
                target = transition_ticket(current["state"],payload.state,decision=payload.decision)
            except ValueError as exc:
                raise HTTPException(409,str(exc)) from None
            if target == "RESOLVED" and not payload.resolution:
                raise HTTPException(422,"resolution text is required")
            from .storage import encrypt_document
            resolution=encrypt_document(payload.resolution.encode(),_document_key(),ticket_id) if payload.resolution else None
            conn.execute("UPDATE ticket SET state=%s,decision=%s,resolution_ciphertext=%s,updated_at=now() WHERE id=%s",
                         (target,payload.decision,resolution,ticket_id))
            conn.execute("INSERT INTO ticket_event(id,ticket_id,actor,action,created_at) VALUES (%s,%s,%s,%s,now())",
                         (uuid4(),ticket_id,principal.email,f"STATE:{target}"))
            conn.execute("INSERT INTO audit_event(id,actor,action,object_type,object_id,request_id,result) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                         (uuid4(),principal.email,"TICKET_UPDATED","ticket",ticket_id,uuid4(),target))
    return {"id":str(ticket_id),"state":target}


@app.get("/api/v1/resumes")
async def list_resumes(principal: Principal = Depends(_owner)):
    if config.DEMO_MODE:
        return {"mode":"DEMO","items":[]}
    with db.connection() as conn:
        items=conn.execute("""SELECT id::text,version,label,category,sha256,mime_type,size_bytes,
          canonical,active,imported_at FROM resume ORDER BY imported_at DESC""").fetchall()
    return {"items":items}


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
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    display_name = safe_display_name(file.filename or "resume.pdf")
    key_file = os.getenv("DOCUMENT_ENCRYPTION_KEY_FILE")
    if not key_file:
        raise HTTPException(503, "document encryption unavailable")
    key = load_key_file(key_file)
    # Check the database prerequisites before writing a private file. This avoids
    # orphaned encrypted documents when profile setup has not been completed.
    with db.connection() as conn:
        profile = conn.execute("SELECT id FROM candidate_profile ORDER BY created_at LIMIT 1").fetchone()
        if not profile:
            raise HTTPException(409, "profile setup is required before importing a resume")
        has_canonical = conn.execute("SELECT 1 FROM resume WHERE profile_id=%s AND canonical AND active LIMIT 1",
                                     (profile["id"],)).fetchone() is not None
        imported_at = datetime.now(timezone.utc)
        doc_id, storage_key, sha256 = store_private(data, display_name, config.DOCUMENT_ROOT, key)
        try:
          with conn.transaction():
            conn.execute("""INSERT INTO resume(id,profile_id,version,label,category,sha256,mime_type,size_bytes,
              storage_key,canonical,active,imported_at,provenance) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
              (doc_id,profile["id"],"resume-upload-"+doc_id.hex[:8],display_name,"Uncategorized",sha256,
               "application/pdf",len(data),storage_key,not has_canonical,not has_canonical,imported_at,
               Jsonb({"imported_by":principal.email,"source":"owner-upload"})))
            conn.execute("""INSERT INTO document(id,profile_id,document_type,sha256,mime_type,size_bytes,
              storage_key,encrypted,provenance) VALUES (%s,%s,%s,%s,%s,%s,%s,true,%s)""",
              (doc_id,profile["id"],"CANONICAL_RESUME" if not has_canonical else "OTHER",sha256,"application/pdf",len(data),storage_key,
               Jsonb({"filename":display_name,"imported_by":principal.email})))
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
            "imported_at":imported_at.isoformat(),
            "audit":"Document imported; content excluded from logs"}


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
