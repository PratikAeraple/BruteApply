# Google Sheets integration

PostgreSQL is authoritative. The existing `Applications`, `Dashboard`, and `Lists` tabs are preserved. `Applications` must retain its 32-column contract, row-derived Application ID, and formula-owned Days Since Applied / Days to Follow-up cells.

Production synchronization runs in the dedicated `sheets-worker` Compose service on the `google-sheets` Celery queue. Only that service receives the file-backed Google OAuth secret. The discovery worker, API, frontend and scheduler do not mount it. The worker runs as UID 10001, has no host port or Docker socket, and uses the internal database/Redis network plus a dedicated outbound network. The transport pins requests to Google's OAuth and Sheets API origins.

Stable row identity uses row-bound Google Sheets developer metadata containing the BruteApply application UUID. Canonical job URL matching is only a recovery lookup; visible Application ID and row numbers are not permanent identities. Google keeps developer metadata attached to its row when rows are inserted or sorted; deleting a row deletes its metadata, after which the exact canonical URL is used as a guarded recovery lookup. Duplicate URLs, occupied mappings, changed system-owned cells, and stale status changes create encrypted conflict records rather than silent overwrites.

On first association only, if a legacy row's exact canonical URL and job identity match but its historical `Date Found` differs from BruteApply's later discovery/import timestamp, the existing Sheet date is preserved. The compared values are encrypted in a resolved audit conflict (`LEGACY_SHEET_VALUE_PRESERVED`) and the column is recorded on the mapping so later syncs do not replace that legacy date. Any other first-association system-field discrepancy remains an open conflict.

Writes are sparse and use `RAW` input mode so untrusted ATS text cannot become formulas. Formula columns, formatting, validation, notes, tabs and unrelated cells are checked against a current full export after writes. Before a row-creating write, the worker encrypts the current export into the private documents volume and verifies it by decrypting and checking its checksum. A private receipt records source identity, timestamp, tabs and checksum. This is a recovery snapshot, not an independent production backup; production backups remain deferred.

Owner-editable values are encrypted in `sheet_owner_state` and returned only through authenticated BruteApply APIs. A Sheet status of “Applied” becomes `SUBMISSION_UNVERIFIED`; no Sheet value is evidence of a verified submission. Stale edits cannot move advanced application states back to “To Apply”. Conflict resolution is explicit and audited.

The worker uses bounded exponential retries, records dead letters without provider response bodies or candidate values, and serializes sync through a PostgreSQL advisory lock. A conservative four-hour schedule is active only after the controlled production sync succeeds and the `sheet_sync` agent remains enabled and unpaused.

The OAuth consent screen is External / Testing. Google may expire refresh tokens after seven days in that state for non-basic scopes. In Google Cloud Console, open **Google Auth Platform → Audience**, change **Publishing status** from **Testing** to **In production**, and save. Monitor token refresh afterward. If Google rejects the existing refresh token, run the owner-attended OAuth flow again and replace the credential through the protected BruteHost provisioning process. Never share OAuth values in chat.

## Initial authorization bootstrap

The owner-attended loopback bootstrap is `backend/scripts/google_oauth_local.py`; it uses PKCE and does not print authorization codes or tokens. Provision its output only through the protected runtime-secret process. Never place OAuth state in this repository.
