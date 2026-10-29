# Security controls

## Current controls

- Production API requires an exact Cloudflare Access issuer, audience, valid JWT and owner email. Missing or invalid assertions fail closed.
- State-changing production API requests require `Origin: https://jobapply.brutehack.com`; use the exact host and keep the Access boundary in front of the app.
- Trusted Host allowlist, no API docs in production, non-cacheable sensitive responses, restrictive security headers, no reflected HTML, and parameterized database queries.
- Profile fact values and uploaded PDFs are encrypted with AES-GCM. Files use random UUID names, private directories, restrictive file modes, PDF signature/size checks, and no public document directory.
- Submission is disabled by default. No provider currently submits applications. CAPTCHA/MFA, unknown facts, sensitive declarations, duplicate uncertainty, disallowed platforms, and missing documents block submission.
- Public demo mode uses synthetic records and has no production integrations or external-write paths.
- Repository ignores private documents, databases, runtime data, secret files, logs and local configuration.

## Deployment requirements

Before production: exact-host Access application for `jobapply.brutehack.com`; verify JWT audience; provision DB, Redis, encryption key, and integration credentials through protected secret files; enforce request limits at proxy/API; use isolated private networks; configure logs to omit request bodies and sensitive values; add rate limits and alerting; and run the documented threat-model checks.

Never expose PostgreSQL/Redis, Docker API/socket, documents, or worker control surfaces. Do not broaden an existing Access policy. Keep emergency stop effective even when AI/providers are unavailable.

## Reporting

Report vulnerabilities privately to the repository owner. Do not include real candidate documents, employer data, OAuth tokens, cookies or production log excerpts in an issue.
