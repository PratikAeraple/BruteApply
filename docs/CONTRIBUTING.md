# Contributing

Use Python 3.12+ and Node.js 20.9+ (24 LTS recommended). Run the backend tests, frontend lint/typecheck/build, secret scan and container configuration validation before submitting changes.

Never use real candidate data in tests or issues. Use synthetic fixtures. Do not add live credentials, OAuth tokens, documents, sheet exports, databases, logs or generated personal application materials. Changes to job sources must document terms, authorization, SSRF protection and request limits. Submission adapters require deterministic policy and fake-transport tests; never test against a real employer.
