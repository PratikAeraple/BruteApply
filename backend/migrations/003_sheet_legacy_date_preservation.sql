-- Existing tracker rows may predate BruteApply and have an owner-maintained
-- Date Found value that differs from a later import/discovery timestamp.
-- Preserve that single legacy field on first association; all other system
-- fields continue to use normal conflict detection.
ALTER TABLE sheet_mapping
  ADD COLUMN IF NOT EXISTS preserved_sheet_fields INTEGER[] NOT NULL DEFAULT '{}'::INTEGER[];
