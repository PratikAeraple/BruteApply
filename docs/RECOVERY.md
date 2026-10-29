# Recovery and backup boundary

Recovery procedures are **designed only**. BruteHost production backups remain **DEFERRED / NOT IMPLEMENTED**; no production repository, destination, backup schedule, or restore validation exists.

Future protected dataset must include PostgreSQL (consistent dump or database-native backup), uploaded/canonical/tailored resumes, generated cover letters and application documents, private candidate facts, encrypted OAuth/integration state, audit metadata, and critical worker/idempotency state. Reproducible source is restored from Git; secrets must be separately re-provisioned or recovered from an owner-approved encrypted secret backup.

Recovery order: provision supported Ubuntu and Docker; retrieve Infrastructure source; restore host directories/ownership and protected secrets; restore PostgreSQL and documents from a verified encrypted backup; restore Redis only if needed (otherwise replay durable DB-backed work); bring up DB/cache; run migrations/consistency checks; start API/workers; validate exact Access/Tunnel route; verify Sheet sync cursor without writing stale data; validate monitoring and regression tests.

No achieved RPO/RTO is claimed. Determine targets with the owner and measure only after restore drills on disposable data. Do not represent local resume staging as production backup.
