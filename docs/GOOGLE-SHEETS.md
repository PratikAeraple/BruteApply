# Google Sheets integration

The existing spreadsheet is preserved and remains an owner-facing projection; PostgreSQL is intended as the operational source of truth. Existing tabs are `Applications`, `Dashboard`, and `Lists`. The 32 Applications columns and row-derived formulas are captured in `backend/app/domain/sheet_sync.py`.

Formula cells are not part of database write payloads. System-owned values, owner-owned values and conflict-checked mergeable values are explicitly separated. Writes must be sparse to avoid clearing formulas. Sync must be idempotent, cursor-based, retryable with backoff, audited and conflict-visible.

The current runtime OAuth is **not configured**. A first production write requires least-privilege Google authorization and a supported private Drive/Sheets snapshot/export before the small controlled sync test. Never use a mailbox password or commit OAuth state. No Sheet rows have been written by this project.
