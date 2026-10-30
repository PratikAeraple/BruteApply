# Job sources

The source boundary is a provider/plugin architecture. Current source code supports owner-mediated manual import plus the public Greenhouse Job Board and Lever Postings APIs. No restricted-platform access, CAPTCHA bypass, or application submission is enabled.

The read-only public-board adapter uses Greenhouse's documented public Job Board GET endpoints and Lever's published-postings API. Greenhouse describes public job-list GET endpoints as unauthenticated career-site data ([official documentation](https://docs.greenhouse.io/job-board)); Lever describes the API as public published postings and notes those may be scraped by third parties ([official documentation](https://github.com/lever/postings-api)). Adapters accept validated board slugs and fixed HTTPS vendor hosts, do not follow redirects, enforce response size/time limits, parse text rather than render provider HTML, and mark returned posting content untrusted. The importer keeps only requested support/infrastructure titles in Ontario, Alberta, Manitoba, or clearly Canada-eligible remote locations.

Future feed/API adapters must declare host and terms policy, use bounded requests and timeouts, validate redirect destinations and resolved IP addresses to prevent SSRF, record source evidence, normalize and deduplicate postings, and treat returned fields as untrusted content. LinkedIn, Indeed, Glassdoor, Workday, or any other platform may only be automated where its current terms and authorized API permit it.

If a platform requires interactive authentication, MFA, CAPTCHA, or disallows automated submission, create an owner ticket and leave the workflow manual.

Submission-handler capability selection is implemented as a fail-closed policy module with a fake adapter for tests only. No real employer handler is enabled. Provider evidence must be idempotent and reliably confirm the submission before application state can become `SUBMITTED`.

Email classification is a deterministic, bounded text classifier. Conflicting signals and common prompt-injection instructions become `NEEDS_REVIEW`; email text never triggers tool calls or actions. Gmail and calendar OAuth/runtime workers remain unconfigured.
## Scheduled public ATS discovery

The production worker runs three bounded scans per day (07:15, 12:15 and 17:15 America/Toronto). It queries only fixed Greenhouse/Lever API hosts and configured public board slugs; redirects and posting URLs are never fetched. The worker has a dedicated outbound Docker network; database/cache networks remain private and only the worker attaches to egress. Each board is explicitly terms-reviewed and enabled. The worker rechecks permission immediately before persistence, deduplicates by canonical URL, external ID and a transaction-serialized content fingerprint, stores a source snapshot, and scores only from verified/private resume facts. It does not apply or contact employers.

### Initial production observation

The initial owner-reviewed public-board sources are D2L (Greenhouse), Maple (Lever), and BuildOps (Greenhouse). The first controlled run returned four in-scope postings. Three passed the deterministic qualification threshold; one remained visible but unqualified due to an unconfirmed French requirement. Two high-scoring, blocker-free roles were recorded as `SHORTLISTED`; a third qualified role remained a possible match below the shortlist threshold. These are source observations, not employment outcomes. No Google Sheet write or AI-generated document has occurred because the required production authorizations are not configured.

Manual owner imports remain available separately. Restricted job sites and sites requiring login, CAPTCHA, or anti-bot workarounds are not automated.
