# Google Sheets integration

The existing spreadsheet is preserved and remains an owner-facing projection; PostgreSQL is intended as the operational source of truth. Existing tabs are `Applications`, `Dashboard`, and `Lists`. The 32 Applications columns and row-derived formulas are captured in `backend/app/domain/sheet_sync.py`.

Formula cells are not part of database write payloads. System-owned values, owner-owned values and conflict-checked mergeable values are explicitly separated. Writes must be sparse to avoid clearing formulas. Sync must be idempotent, cursor-based, retryable with backoff, audited and conflict-visible.

The current runtime OAuth is **not configured**. `backend/app/integrations/google_oauth_rest.py` implements fixed-origin Sheets values read/write and Drive snapshot calls. Refresh credentials are read only from a mode-0600 runtime file. The owner-attended loopback bootstrap is `backend/scripts/google_oauth_local.py`; it uses PKCE and does not print authorization codes or tokens.

The OAuth scopes are `spreadsheets` and `drive.file`. The Sheets scope is account-wide for spreadsheets; application code pins requests to the configured tracker ID but OAuth itself cannot restrict that scope to one spreadsheet. `drive.file` is narrower and may require the owner to grant the app access to the existing tracker. If the snapshot API cannot copy the existing file with the authorized scope, stop before any Sheet write and resolve the permission through Google's consent/file-access flow. Do not broaden scopes silently.

A first production write requires a verified native Google Sheets snapshot and read-back validation of the existing Applications, Dashboard, and Lists tabs. The sync adapter requires a `SnapshotProof` for non-dry-run writes, validates the 32-column schema, and emits sparse cell updates that exclude Application ID, Days Since Applied, and Days to Follow-up formulas. Never use a mailbox password or commit OAuth state. No Sheet rows have been written by this project.

## Owner authorization bootstrap

1. Create or select a Google OAuth client appropriate for an installed/loopback application and allow `http://127.0.0.1:8765/callback` as its redirect URI.
2. Place the downloaded OAuth client JSON in owner-only local state and set mode `0600`; do not place it in this repository.
3. Run `python backend/scripts/google_oauth_local.py --oauth-client-file /protected/path/client.json` on the owner's workstation. Review Google's consent screen and grant only the listed BruteApply scopes.
4. The script creates `~/.local/state/bruteapply/google-oauth.json` with mode `0600`. Provision that file to the private runtime using the approved secret provisioning process. Never paste it into ChatGPT, terminal arguments, documentation, or Git.
5. Verify read-only access and complete a private backup/snapshot before enabling the first write.
