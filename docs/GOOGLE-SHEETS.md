# Google Sheets integration

PostgreSQL is authoritative. The existing `Applications`, `Dashboard`, and `Lists` tabs are preserved. `Applications` must retain its 32-column contract, row-derived Application ID, and formula-owned Days Since Applied / Days to Follow-up cells.

Production synchronization runs in the dedicated `sheets-worker` Compose service on the `google-sheets` Celery queue. Only that service receives the file-backed Google OAuth secret. The discovery worker, API, frontend and scheduler do not mount it. The worker runs as UID 10001, has no host port or Docker socket, and uses the internal database/Redis network plus a dedicated outbound network. The transport pins requests to Google's OAuth and Sheets API origins.

Stable row identity uses row-bound Google Sheets developer metadata containing the BruteApply application UUID. Canonical job URL matching is only a recovery lookup; visible Application ID and row numbers are not permanent identities. Google keeps developer metadata attached to its row when rows are inserted or sorted; deleting a row deletes its metadata, after which the exact canonical URL is used as a guarded recovery lookup. Duplicate URLs, occupied mappings, changed system-owned cells, and stale status changes create encrypted conflict records rather than silent overwrites.

On first association only, if a legacy row's exact canonical URL and job identity match but its historical `Date Found` differs from BruteApply's later discovery/import timestamp, the existing Sheet date is preserved. The compared values are encrypted in a resolved audit conflict (`LEGACY_SHEET_VALUE_PRESERVED`) and the column is recorded on the mapping so later syncs do not replace that legacy date. Any other first-association system-field discrepancy remains an open conflict.

Writes are sparse and use `RAW` input mode so untrusted ATS text cannot become formulas. Formula columns, formatting, validation, notes, tabs and unrelated cells are checked against a current full export after writes. Before a row-creating write, the worker encrypts the current export into the private documents volume and verifies it by decrypting and checking its checksum. A private receipt records source identity, timestamp, tabs and checksum. This is a recovery snapshot, not an independent production backup; production backups remain deferred.

Owner-editable values are encrypted in `sheet_owner_state` and returned only through authenticated BruteApply APIs. A Sheet status of “Applied” becomes `SUBMISSION_UNVERIFIED`; no Sheet value is evidence of a verified submission. Stale edits cannot move advanced application states back to “To Apply”. Conflict resolution is explicit and audited.

The worker uses bounded exponential retries, records dead letters without provider response bodies or candidate values, and serializes sync through a PostgreSQL advisory lock. A conservative four-hour schedule is active only after the controlled production sync succeeds and the `sheet_sync` agent remains enabled and unpaused.

## OAuth scope and credential rotation

The connector requires only `https://www.googleapis.com/auth/spreadsheets`. Its reads, sparse value writes, spreadsheet metadata reads, and developer-metadata lookup/update operations all use the Sheets API. Pre-write snapshots are encrypted locally in BruteApply's private documents storage; the production sync does not copy spreadsheets through Drive. The broader `drive.file` grant is not requested by the current connector. The Sheets scope is sensitive and grants access to the user's spreadsheets; it is not limited to the one configured tracker.

The Google OAuth consent screen is owner-confirmed **External / In production**. The source and Compose defaults now match that status; the previous Testing defaults were stale. This publishing status avoids the seven-day refresh-token expiration rule that applies to External / Testing projects with scopes beyond basic identity. Still monitor token refresh and consent health.

The local owner-attended PKCE bootstrap requests Sheets only and disables incremental scope accumulation. Before saving, it introspects the issued access token and requires the actual grant to contain exactly the Sheets scope; this catches older Drive grants that remain associated with a reused OAuth client. If an existing client continues to issue broader scopes, create a distinct OAuth client ID in the same production-published Google Cloud project and authorize that client instead. Its default output is `~/.local/state/bruteapply/google-oauth-sheets-only.json`, separate from the existing `google-oauth.json`, and it refuses to overwrite any existing output. The credential remains owner-controlled until deliberately transferred through the protected provisioning process.

Preparing this narrower credential does **not** rotate or replace production credentials. A token authorized with the current OAuth client was observed to retain both Sheets and `drive.file` grants even with incremental scope accumulation disabled. The currently working production OAuth file, the dedicated `sheets-worker`, scheduled sync, spreadsheet rows, formulas, formatting, validation, dropdowns, and mapping remain unchanged. A later activation is a separate attended operation: obtain an actually Sheets-only token (using a distinct client if necessary), stage it privately, validate refresh and tracker access with it, then perform a controlled worker credential swap and post-swap sync check. Never revoke the old grant until the replacement is proven functional. Never share OAuth values in chat or Git.

## Initial authorization bootstrap

The owner-attended loopback bootstrap is `backend/scripts/google_oauth_local.py`; it uses PKCE and does not print authorization codes or tokens. Provision its output only through the protected runtime-secret process. Never place OAuth state in this repository. To create a separate Sheets-only credential on a Mac with a local BruteApply clone at `~/BruteApply` and the downloaded OAuth client file at `~/Downloads/google-oauth-client.json`, run:

```sh
cd "$HOME/BruteApply" && python3 backend/scripts/google_oauth_local.py \
  --oauth-client-file "$HOME/Downloads/google-oauth-client.json" \
  --output "$HOME/.local/state/bruteapply/google-oauth-sheets-only.json"
```

If the clone or downloaded client has a different location, change only that path. The command opens Google's consent page in the local browser and writes the new credential at the separate output path with mode `0600`; it does not alter BruteHost production.
