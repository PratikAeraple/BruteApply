# Threat model

| Threat | Boundary/control | Remaining work |
|---|---|---|
| Unauthenticated Internet access | Exact-host Cloudflare Access and API JWT verification | Access application not yet provisioned |
| Forged Access assertion | Signature, issuer, audience, expiry and owner identity validation | Integration test with real Access JWT after owner setup |
| CSRF | Exact Origin check for mutations; Access cookie remains same-site boundary | Add browser-level production-origin tests |
| SQL injection | Psycopg parameter binding; fixed SQL identifiers | Continue query review |
| XSS | React escaping, no HTML rendering of job descriptions | Add CSP nonce strategy and browser tests before production |
| SSRF | No arbitrary fetch/import URL worker is enabled | Provider adapters must enforce host allowlists, DNS/IP checks and redirect policy |
| Malicious job or email prompt injection | External content treated as untrusted data; AI cannot change deterministic policy | Adversarial provider tests required before AI activation |
| Document path traversal / executable upload | PDF-only signature/size checks, randomized internal path, encryption | Malware scanning remains a future controlled capability |
| OAuth/API key compromise | Secret-file references only, least privilege, no credentials in Git | Owner provisioning and rotation procedure needed |
| Duplicate applications / worker replay | Fingerprints, unique constraints, idempotency policy | Real provider-specific idempotency must be validated |
| Unauthorized autonomous submission | Disabled default, explicit policy gates, emergency stop | One-time owner activation remains mandatory |
| Data loss | PostgreSQL/document dataset documented for future backup | **Production backup deferred; no destination or restore test** |

External postings, recruiter email and model output are data, never authority. Human decisions are ticketed. Sensitive/legal claims cannot be inferred from a job description or model response.
