ALTER TABLE resume ADD COLUMN page_count SMALLINT CHECK (page_count BETWEEN 1 AND 20);

-- Database-enforced deduplication closes races between parallel public ATS
-- scans; duplicates from different sources resolve to the existing job.
CREATE UNIQUE INDEX job_canonical_url_unique_idx ON job(canonical_url);

CREATE TABLE candidate_fact (
  id UUID PRIMARY KEY,
  profile_id UUID NOT NULL REFERENCES candidate_profile(id) ON DELETE CASCADE,
  resume_id UUID REFERENCES resume(id) ON DELETE SET NULL,
  fact_ciphertext BYTEA NOT NULL,
  source_sha256 CHAR(64),
  source_kind TEXT NOT NULL CHECK (source_kind IN ('CANONICAL_RESUME','OWNER_INPUT','APPROVED_ANSWER','VERIFIED_PROJECT')),
  source_ordinal INTEGER,
  page_number SMALLINT,
  section TEXT NOT NULL,
  confidence NUMERIC(4,3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  sensitivity TEXT NOT NULL DEFAULT 'NORMAL' CHECK (sensitivity IN ('NORMAL','SENSITIVE','LEGAL')),
  status TEXT NOT NULL DEFAULT 'NEEDS_REVIEW' CHECK (status IN ('NEEDS_REVIEW','RESUME_VERIFIED','OWNER_APPROVED','REJECTED','SUPERSEDED')),
  provenance JSONB NOT NULL,
  reviewed_by TEXT,
  reviewed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK ((source_kind='CANONICAL_RESUME' AND resume_id IS NOT NULL AND page_number IS NOT NULL AND source_sha256 IS NOT NULL)
      OR source_kind<>'CANONICAL_RESUME')
);
CREATE INDEX candidate_fact_review_idx ON candidate_fact(profile_id,status,section,confidence DESC);
CREATE INDEX candidate_fact_resume_idx ON candidate_fact(resume_id,page_number);
CREATE UNIQUE INDEX candidate_fact_resume_source_idx
  ON candidate_fact(resume_id,source_sha256,source_ordinal) WHERE source_kind='CANONICAL_RESUME';

ALTER TABLE application DROP CONSTRAINT application_state_check;
ALTER TABLE application ADD CONSTRAINT application_state_check CHECK (state IN (
  'DISCOVERED','QUALIFIED','SHORTLISTED','PREPARING','PREPARED','READY','SUBMISSION_PENDING',
  'ATTEMPTED','SUBMISSION_ATTEMPTED','SUBMITTED','SUBMISSION_UNVERIFIED','MANUAL_SUBMISSION_REQUIRED',
  'ASSESSMENT','RECRUITER_SCREEN','INTERVIEW_1','INTERVIEW_2','FINAL_INTERVIEW','OFFER',
  'ON_HOLD','REJECTED','WITHDRAWN'
));
