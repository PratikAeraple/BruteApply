# Contributing

Contributions should preserve the privacy and safety boundaries in `SECURITY.md`, `THREAT-MODEL.md` and `APPLICATION-POLICY.md`.

- Use synthetic fixtures only.
- Do not add real personal data or credentials.
- Add tests for policy, authorization, idempotency and failure behavior.
- Keep job-source adapters within the provider's published terms and access model.
- Do not add CAPTCHA/MFA bypass, stealth automation, mass-application behavior or hidden submission.
- Run backend tests, frontend lint/build, dependency checks and the repository privacy scan before a pull request.

Changes that can cause an external application, email, calendar edit or Sheet write require a fake provider and an explicit test proving it cannot reach a real account.
