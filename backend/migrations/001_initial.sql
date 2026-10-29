CREATE TABLE candidate_profile (
  id UUID PRIMARY KEY,
  facts_ciphertext BYTEA NOT NULL,
  facts_key_version SMALLINT NOT NULL DEFAULT 1,
  schema_version SMALLINT NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX one_candidate_profile ON candidate_profile ((true));

CREATE TABLE profile_fact (
  id UUID PRIMARY KEY,
  profile_id UUID NOT NULL REFERENCES candidate_profile(id) ON DELETE CASCADE,
  fact_key TEXT NOT NULL CHECK (length(fact_key) BETWEEN 1 AND 100),
  value_ciphertext BYTEA NOT NULL,
  verified BOOLEAN NOT NULL DEFAULT false,
  source TEXT NOT NULL,
  approved_at TIMESTAMPTZ,
  expires_at TIMESTAMPTZ,
  sensitivity TEXT NOT NULL DEFAULT 'NORMAL' CHECK (sensitivity IN ('NORMAL','SENSITIVE','LEGAL')),
  UNIQUE(profile_id, fact_key)
);

CREATE TABLE resume (
  id UUID PRIMARY KEY,
  profile_id UUID NOT NULL REFERENCES candidate_profile(id),
  version TEXT NOT NULL,
  label TEXT NOT NULL,
  category TEXT NOT NULL,
  sha256 CHAR(64) NOT NULL,
  mime_type TEXT NOT NULL CHECK (mime_type='application/pdf'),
  size_bytes BIGINT NOT NULL CHECK (size_bytes BETWEEN 1 AND 15728640),
  storage_key TEXT NOT NULL UNIQUE,
  canonical BOOLEAN NOT NULL DEFAULT false,
  active BOOLEAN NOT NULL DEFAULT false,
  imported_at TIMESTAMPTZ NOT NULL,
  provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(profile_id, version)
);
CREATE UNIQUE INDEX one_active_canonical_resume ON resume(profile_id) WHERE canonical AND active;

CREATE TABLE job_source (
  id UUID PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  source_type TEXT NOT NULL CHECK (source_type IN ('FEED','API','MANUAL','OWNER_IMPORT')),
  permitted BOOLEAN NOT NULL DEFAULT false,
  terms_reviewed_at TIMESTAMPTZ,
  enabled BOOLEAN NOT NULL DEFAULT false,
  configuration JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE job (
  id UUID PRIMARY KEY,
  source_id UUID REFERENCES job_source(id),
  company TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  city TEXT,
  province TEXT,
  country TEXT NOT NULL DEFAULT 'Canada',
  work_mode TEXT,
  employment_type TEXT,
  salary_min NUMERIC(12,2),
  salary_max NUMERIC(12,2),
  currency CHAR(3),
  canonical_url TEXT NOT NULL,
  external_id TEXT,
  posting_fingerprint CHAR(64) NOT NULL,
  description_fingerprint CHAR(64) NOT NULL,
  posted_at TIMESTAMPTZ,
  deadline TIMESTAMPTZ,
  requirements JSONB NOT NULL DEFAULT '{}'::jsonb,
  source_evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
  lifecycle TEXT NOT NULL DEFAULT 'DISCOVERED',
  discovered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(source_id, external_id)
);
CREATE INDEX job_fingerprint_idx ON job(posting_fingerprint);
CREATE INDEX job_company_title_idx ON job(lower(company), lower(title));
CREATE INDEX job_discovered_idx ON job(discovered_at DESC);

CREATE TABLE job_snapshot (
  id UUID PRIMARY KEY,
  job_id UUID NOT NULL REFERENCES job(id) ON DELETE CASCADE,
  captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  content_hash CHAR(64) NOT NULL,
  content JSONB NOT NULL,
  UNIQUE(job_id, content_hash)
);

CREATE TABLE job_match (
  id UUID PRIMARY KEY,
  job_id UUID NOT NULL REFERENCES job(id) ON DELETE CASCADE,
  profile_id UUID NOT NULL REFERENCES candidate_profile(id),
  score SMALLINT NOT NULL CHECK (score BETWEEN 0 AND 100),
  confidence NUMERIC(4,3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  matched JSONB NOT NULL,
  missing JSONB NOT NULL,
  hard_blockers JSONB NOT NULL,
  explanation TEXT NOT NULL,
  recommended_action TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(job_id, profile_id)
);

CREATE TABLE application (
  id UUID PRIMARY KEY,
  profile_id UUID NOT NULL REFERENCES candidate_profile(id),
  job_id UUID NOT NULL REFERENCES job(id),
  idempotency_key TEXT NOT NULL UNIQUE,
  state TEXT NOT NULL CHECK (state IN ('DISCOVERED','QUALIFIED','SHORTLISTED','PREPARED','ATTEMPTED','SUBMITTED','SUBMISSION_UNVERIFIED','MANUAL_SUBMISSION_REQUIRED','REJECTED','WITHDRAWN')),
  sheet_row INTEGER,
  sheet_visible_id TEXT,
  date_applied TIMESTAMPTZ,
  resume_id UUID REFERENCES resume(id),
  cover_letter_document_id UUID,
  confirmation_reference_ciphertext BYTEA,
  submitted_evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
  owner_notes_ciphertext BYTEA,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(profile_id, job_id)
);
CREATE INDEX application_state_idx ON application(state, updated_at DESC);

CREATE TABLE document (
  id UUID PRIMARY KEY,
  profile_id UUID NOT NULL REFERENCES candidate_profile(id),
  application_id UUID REFERENCES application(id),
  job_id UUID REFERENCES job(id),
  document_type TEXT NOT NULL CHECK (document_type IN ('CANONICAL_RESUME','TAILORED_RESUME','COVER_LETTER','SCREENING_RESPONSE','APPLICATION_SUMMARY','OTHER')),
  sha256 CHAR(64) NOT NULL,
  mime_type TEXT NOT NULL,
  size_bytes BIGINT NOT NULL CHECK (size_bytes BETWEEN 1 AND 15728640),
  storage_key TEXT NOT NULL UNIQUE,
  encrypted BOOLEAN NOT NULL DEFAULT true,
  resume_base_id UUID REFERENCES resume(id),
  provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE application ADD CONSTRAINT application_cover_letter_fk FOREIGN KEY (cover_letter_document_id) REFERENCES document(id);

CREATE TABLE application_event (
  id UUID PRIMARY KEY,
  application_id UUID NOT NULL REFERENCES application(id) ON DELETE CASCADE,
  from_state TEXT,
  to_state TEXT NOT NULL,
  actor TEXT NOT NULL,
  evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE screening_question (
  id UUID PRIMARY KEY,
  job_id UUID REFERENCES job(id),
  application_id UUID REFERENCES application(id),
  question_ciphertext BYTEA NOT NULL,
  classification TEXT NOT NULL,
  sensitivity TEXT NOT NULL DEFAULT 'NORMAL',
  state TEXT NOT NULL DEFAULT 'OPEN',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE approved_answer (
  id UUID PRIMARY KEY,
  question_pattern TEXT NOT NULL,
  canonical_question TEXT NOT NULL,
  answer_ciphertext BYTEA NOT NULL,
  scope TEXT NOT NULL,
  source TEXT NOT NULL,
  approved_at TIMESTAMPTZ NOT NULL,
  expires_at TIMESTAMPTZ,
  sensitivity TEXT NOT NULL,
  notes_ciphertext BYTEA,
  active BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE ticket (
  id UUID PRIMARY KEY,
  category TEXT NOT NULL,
  priority TEXT NOT NULL CHECK (priority IN ('LOW','NORMAL','HIGH','CRITICAL')),
  state TEXT NOT NULL DEFAULT 'OPEN' CHECK (state IN ('OPEN','IN_PROGRESS','RESOLVED','CANCELLED')),
  job_id UUID REFERENCES job(id),
  application_id UUID REFERENCES application(id),
  summary_ciphertext BYTEA NOT NULL,
  evidence_ciphertext BYTEA NOT NULL,
  required_action_ciphertext BYTEA NOT NULL,
  decision TEXT,
  resolution_ciphertext BYTEA,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ticket_open_idx ON ticket(state, priority, created_at DESC);

CREATE TABLE ticket_event (
  id UUID PRIMARY KEY,
  ticket_id UUID NOT NULL REFERENCES ticket(id) ON DELETE CASCADE,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  detail_ciphertext BYTEA,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE integration (
  id UUID PRIMARY KEY,
  provider TEXT NOT NULL UNIQUE,
  state TEXT NOT NULL DEFAULT 'NOT_CONFIGURED',
  credential_reference TEXT,
  encrypted_state BYTEA,
  scopes JSONB NOT NULL DEFAULT '[]'::jsonb,
  last_success_at TIMESTAMPTZ,
  last_error_category TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE sync_state (
  id UUID PRIMARY KEY,
  integration_id UUID NOT NULL REFERENCES integration(id),
  resource TEXT NOT NULL,
  cursor TEXT,
  last_success_at TIMESTAMPTZ,
  last_attempt_at TIMESTAMPTZ,
  retry_count INTEGER NOT NULL DEFAULT 0,
  dead_letter_count INTEGER NOT NULL DEFAULT 0,
  error_category TEXT,
  UNIQUE(integration_id, resource)
);

CREATE TABLE sheet_mapping (
  id UUID PRIMARY KEY,
  application_id UUID NOT NULL UNIQUE REFERENCES application(id),
  sheet_id TEXT NOT NULL,
  sheet_row INTEGER NOT NULL,
  visible_application_id TEXT,
  last_sheet_hash CHAR(64),
  last_database_hash CHAR(64),
  last_synced_at TIMESTAMPTZ,
  conflict_state TEXT,
  UNIQUE(sheet_id, sheet_row)
);

CREATE TABLE email_event (
  id UUID PRIMARY KEY,
  provider_message_id TEXT NOT NULL UNIQUE,
  classification TEXT NOT NULL,
  application_id UUID REFERENCES application(id),
  ticket_id UUID REFERENCES ticket(id),
  received_at TIMESTAMPTZ NOT NULL,
  processed_at TIMESTAMPTZ,
  content_ciphertext BYTEA,
  state TEXT NOT NULL DEFAULT 'UNPROCESSED'
);

CREATE TABLE calendar_event (
  id UUID PRIMARY KEY,
  provider_event_id TEXT UNIQUE,
  application_id UUID REFERENCES application(id),
  title_ciphertext BYTEA,
  starts_at TIMESTAMPTZ,
  ends_at TIMESTAMPTZ,
  state TEXT NOT NULL DEFAULT 'NEEDS_REVIEW',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE worker_job (
  id UUID PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  task_name TEXT NOT NULL,
  state TEXT NOT NULL,
  attempts SMALLINT NOT NULL DEFAULT 0,
  scheduled_at TIMESTAMPTZ,
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  error_category TEXT,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE audit_event (
  id UUID PRIMARY KEY,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  object_type TEXT NOT NULL,
  object_id UUID,
  request_id UUID NOT NULL,
  result TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX audit_created_idx ON audit_event(created_at DESC);

CREATE TABLE api_rate_limit (
  actor_hash CHAR(64) NOT NULL,
  route_class TEXT NOT NULL,
  window_start TIMESTAMPTZ NOT NULL,
  request_count INTEGER NOT NULL CHECK (request_count > 0),
  PRIMARY KEY(actor_hash, route_class)
);

CREATE TABLE agent_configuration (
  id UUID PRIMARY KEY,
  agent TEXT NOT NULL UNIQUE,
  enabled BOOLEAN NOT NULL DEFAULT false,
  paused BOOLEAN NOT NULL DEFAULT true,
  drain BOOLEAN NOT NULL DEFAULT false,
  emergency_stopped BOOLEAN NOT NULL DEFAULT true,
  configuration JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_by TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO agent_configuration(id,agent,enabled,paused,drain,emergency_stopped,updated_by) VALUES
 ('10000000-0000-4000-8000-000000000001','discovery',false,true,false,false,'system'),
 ('10000000-0000-4000-8000-000000000002','matching',false,true,false,false,'system'),
 ('10000000-0000-4000-8000-000000000003','documents',false,true,false,false,'system'),
 ('10000000-0000-4000-8000-000000000004','submission',false,true,false,true,'system'),
 ('10000000-0000-4000-8000-000000000005','sheet_sync',false,true,false,false,'system'),
 ('10000000-0000-4000-8000-000000000006','email',false,true,false,false,'system'),
 ('10000000-0000-4000-8000-000000000007','followups',false,true,false,false,'system');

INSERT INTO job_source(id,name,source_type,permitted,terms_reviewed_at,enabled)
VALUES ('00000000-0000-4000-8000-000000000001','owner-import','OWNER_IMPORT',true,now(),true);
