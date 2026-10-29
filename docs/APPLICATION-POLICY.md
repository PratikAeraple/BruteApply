# Application policy

The submission gate is deterministic and fail-closed. It requires threshold score, enabled one-time owner activation, a permitted adapter/platform, complete owner-verified required information, no unanswered sensitive question, passing duplicate checks, valid documents, and no CAPTCHA/MFA/authentication boundary. First failing condition blocks submission with a ticket category.

Attempted does not mean submitted. Only reliable evidence with a confirmation ID can produce `SUBMITTED`; otherwise use `SUBMISSION_UNVERIFIED`. No real employer test applications are allowed. Current autonomous submission is disabled and no permitted submission adapter is implemented.

Conservative policy limits are configurable. Never infer protected or legal facts, salary commitments, clearance, or work authorization. Owner-approved answers require scope, source, approval and expiry metadata.
