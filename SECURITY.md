# Security policy

## Supported deployment

Use a private, owner-authenticated deployment. Never expose the app, database, Redis or document storage anonymously. The public repository's demo mode contains synthetic data only and must not be connected to production integrations.

## Reporting vulnerabilities

Do not open public issues containing personal information, credentials, OAuth tokens, access assertions, application data or exploit payloads against a live service. Report a concise description privately to the repository owner through GitHub's private vulnerability reporting when available.

## Security controls

- Cloudflare Access JWT is cryptographically validated at the API boundary; the owner email and exact audience are checked.
- PostgreSQL and Redis are private services without published ports.
- Uploads are size- and type-limited, signature-checked, randomly named and stored outside the web root.
- Integration credentials use secret files and encrypted-at-rest credential records.
- Job descriptions and email are treated as hostile content; model output cannot override deterministic policy.
- External submission is disabled by default and never bypasses CAPTCHA, MFA, login or platform restrictions.
- Logs exclude resume text, message bodies, tokens and answers to sensitive questions.

## Private data warning

Never put personal resumes, generated documents, application records, recruiter correspondence, OAuth artifacts, database dumps or production logs in this public repository.
