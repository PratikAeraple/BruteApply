ALTER TABLE sheet_mapping
  ADD COLUMN IF NOT EXISTS developer_metadata_id BIGINT,
  ADD COLUMN IF NOT EXISTS owner_baseline_ciphertext BYTEA;

ALTER TABLE application DROP CONSTRAINT IF EXISTS application_state_check;
ALTER TABLE application ADD CONSTRAINT application_state_check CHECK (state IN (
  'DISCOVERED','QUALIFIED','SHORTLISTED','PREPARING','PREPARED','READY','SUBMISSION_PENDING',
  'ATTEMPTED','SUBMISSION_ATTEMPTED','SUBMITTED',
  'SUBMISSION_UNVERIFIED','MANUAL_SUBMISSION_REQUIRED','ASSESSMENT','RECRUITER_SCREEN',
  'INTERVIEW_1','INTERVIEW_2','FINAL_INTERVIEW','OFFER','REJECTED','WITHDRAWN','ON_HOLD'
));

CREATE TABLE IF NOT EXISTS sheet_owner_state (
  application_id UUID PRIMARY KEY REFERENCES application(id) ON DELETE CASCADE,
  fields_ciphertext BYTEA NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sheet_sync_conflict (
  id UUID PRIMARY KEY,
  application_id UUID NOT NULL REFERENCES application(id) ON DELETE CASCADE,
  sheet_id TEXT NOT NULL,
  sheet_row INTEGER NOT NULL,
  fields JSONB NOT NULL,
  sheet_values_ciphertext BYTEA NOT NULL,
  database_values_ciphertext BYTEA NOT NULL,
  state TEXT NOT NULL DEFAULT 'OPEN' CHECK (state IN ('OPEN','RESOLVED','IGNORED')),
  resolution TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS sheet_sync_conflict_open_idx
  ON sheet_sync_conflict(state,created_at DESC);
