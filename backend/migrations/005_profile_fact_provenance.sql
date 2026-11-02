ALTER TABLE profile_fact
  ADD COLUMN IF NOT EXISTS provenance JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE profile_fact
  ADD CONSTRAINT profile_fact_provenance_object CHECK (jsonb_typeof(provenance)='object');
