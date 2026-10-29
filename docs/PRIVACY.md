# Privacy

BruteApply handles sensitive job-search and identity-related information. Production must be private and Access-protected. The open-source repository contains synthetic examples only.

Candidate profiles, resumes, generated documents, screening answers, applications, recruiter correspondence and OAuth state belong in private encrypted runtime storage, never Git. Avoid retaining full email/job content when a bounded classification and necessary evidence suffice. Logs must not include personal message bodies, resume contents, tokens or credentials.

The demo stores only synthetic data in the browser bundle. No production integration is active. Production backup is deferred, so operators must not treat the current service as data-protected until a destination and restore have been validated.
