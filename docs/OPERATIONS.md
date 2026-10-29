# Operations

## Safe operating defaults

- Autonomous submission: disabled.
- Discovery: no external source enabled until terms and adapter behavior are reviewed.
- Owner import: manual URL/data only, deduplicated by canonical URL and content fingerprint.
- Backup: **DEFERRED / NOT IMPLEMENTED**. There is no approved destination, initialized Restic repository, or production restore test.
- Times: persist UTC; display America/Toronto.

## Health signals

`/healthz` reports process/application identity. `/readyz` also checks PostgreSQL in production. Neither endpoint is public proof of Access authentication; public uptime monitoring should check the Access boundary while private monitoring checks application/database/worker health.

Watch API, worker, database and Redis health separately. Logs must include correlation IDs and error categories, not request bodies, candidate facts, resume text, tokens, or recruiter email bodies.

## Emergency stop

The production API emergency-stop operation sets submission stopped and paused in the control table. The submission task also refuses work without a permitted adapter. The UI must present the state clearly. Verify the persistent state before any future submission activation.
