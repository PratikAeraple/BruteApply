# Job sources

The source boundary is a provider/plugin architecture. Current source code supports only owner-mediated manual import and a database source registry. No scraping, restricted-platform access, CAPTCHA bypass, or external automated discovery is enabled.

The optional read-only public-board adapter supports Greenhouse's documented Job Board API and Lever's public Postings API. Greenhouse documents the public job list at [`GET /v1/boards/{board_token}/jobs`](https://docs.greenhouse.io/job-board); Lever documents its published-posting list at the [Lever Postings API](https://github.com/lever/postings-api). Adapters accept only validated board slugs and fixed HTTPS vendor hosts, do not follow redirects, enforce response size/time limits, parse text rather than render provider HTML, and mark all returned posting content untrusted. They are not activated until each source is explicitly configured and reviewed.

Future feed/API adapters must declare host and terms policy, use bounded requests and timeouts, validate redirect destinations and resolved IP addresses to prevent SSRF, record source evidence, normalize and deduplicate postings, and treat returned fields as untrusted content. LinkedIn, Indeed, Glassdoor, Workday, or any other platform may only be automated where its current terms and authorized API permit it.

If a platform requires interactive authentication, MFA, CAPTCHA, or disallows automated submission, create an owner ticket and leave the workflow manual.

Submission-handler capability selection is implemented as a fail-closed policy module with a fake adapter for tests only. No real employer handler is enabled. Provider evidence must be idempotent and reliably confirm the submission before application state can become `SUBMITTED`.

Email classification is a deterministic, bounded text classifier. Conflicting signals and common prompt-injection instructions become `NEEDS_REVIEW`; email text never triggers tool calls or actions. Gmail and calendar OAuth/runtime workers remain unconfigured.
## Scheduled public ATS discovery

The production worker runs one conservative daily scan (07:15 America/Toronto). It queries only fixed Greenhouse/Lever API hosts and uses the configured public board slug; redirects and posting URLs are never fetched. Sources are disabled until an owner explicitly records a terms review and enables a board through `PUT /api/v1/integrations/public-ats`. The worker rechecks the permission immediately before persistence, deduplicates by canonical URL, external ID and a transaction-serialized content fingerprint, stores a source snapshot, and scores only from verified/private resume facts. Discovery does not create applications or submit anything.

Manual owner imports remain available separately. Restricted job sites and sites requiring login, CAPTCHA, or anti-bot workarounds are not automated.
