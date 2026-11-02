# Job sources

The source boundary is a provider/plugin architecture. Current source code supports owner-mediated manual import plus the public Greenhouse Job Board, Lever Postings, and SmartRecruiters public posting APIs. No restricted-platform access, CAPTCHA bypass, or application submission is enabled.

The read-only public-board adapter uses Greenhouse's documented public Job Board GET endpoints and Lever's published-postings API. Greenhouse describes public job-list GET endpoints as unauthenticated career-site data ([official documentation](https://docs.greenhouse.io/job-board)); Lever describes the API as public published postings ([official documentation](https://github.com/lever/postings-api)). SmartRecruiters' public Posting API supports company posting lists and detail retrieval ([official documentation](https://developers.smartrecruiters.com/docs/endpoints)). Adapters accept validated board slugs and fixed HTTPS vendor hosts, do not follow redirects, enforce response size/time limits, parse text rather than render provider HTML, and mark returned posting content untrusted. ATS publication timestamps and the original ATS location label are retained as source evidence. When an ATS labels a remote posting broadly, the importer narrows it to Canada only if the title or description explicitly says Canada. The importer keeps only requested support/infrastructure titles in Ontario, Alberta, Manitoba, or clearly Canada-eligible remote locations.

Future feed/API adapters must declare host and terms policy, use bounded requests and timeouts, validate redirect destinations and resolved IP addresses to prevent SSRF, record source evidence, normalize and deduplicate postings, and treat returned fields as untrusted content. LinkedIn, Indeed, Glassdoor, Workday, or any other platform may only be automated where its current terms and authorized API permit it.

If a platform requires interactive authentication, MFA, CAPTCHA, or disallows automated submission, create an owner ticket and leave the workflow manual.

Submission-handler capability selection is implemented as a fail-closed policy module with a fake adapter for tests only. No real employer handler is enabled. Provider evidence must be idempotent and reliably confirm the submission before application state can become `SUBMITTED`.

## Submission capability audit — 2026-11-02

Production submission adapters: **NONE**. The fake handler is test-only and must never be reported as an employer submission. Existing Greenhouse, Lever, and SmartRecruiters integrations are public posting discovery only.

- Lever documents a candidate application POST, but requires an API key generated from the employer account’s integrations settings; its public postings API is not that key. BruteApply has no employer-issued key or authorization for the currently tracked employers. See the [official Lever Postings API application section](https://github.com/lever/postings-api#apply-to-a-job-posting).
- Greenhouse’s application creation endpoints are part of its authenticated employer-side APIs. Public job-board access does not grant applicant-submission authorization. See [Greenhouse application creation](https://harvestdocs.greenhouse.io/reference/post_v3-applications) and its [Harvest API](https://docs.greenhouse.io/harvest.html).
- SmartRecruiters Apply requires the employer/partner `candidate_applications_manage` scope; no such authorization exists in this candidate workspace. See [SmartRecruiters Apply API authorization](https://developers.smartrecruiters.com/changelog/oauth-2-support-apply-api) and [Create a New Candidate Application](https://developers.smartrecruiters.com/reference/createcandidate-1).

Therefore current applications must use the official employer-hosted forms manually. No browser automation has been approved by a platform-specific rule review, and no employer form has passed a real end-to-end dry run. Autonomous submission and its owner activation control remain unavailable. Do not request employer API credentials as if they were candidate credentials; use manual handoff unless the employer explicitly authorizes an appropriate integration.

Email classification is a deterministic, bounded text classifier. Conflicting signals and common prompt-injection instructions become `NEEDS_REVIEW`; email text never triggers tool calls or actions. Gmail and calendar OAuth/runtime workers remain unconfigured.
## Scheduled public ATS discovery

The production worker runs three bounded scans per day (07:15, 12:15 and 17:15 America/Toronto). It queries only fixed Greenhouse, Lever, and SmartRecruiters API hosts and explicitly configured public board slugs; redirects and employer posting URLs are never fetched. The worker has a dedicated outbound Docker network; database/cache networks remain private and only the worker attaches to egress. Each board is explicitly terms-reviewed and enabled. The worker rechecks permission immediately before persistence, deduplicates by canonical URL, external ID and a transaction-serialized content fingerprint, stores a source snapshot, and scores only from verified/private resume facts. It does not apply or contact employers.

### Initial production observation

Production source configuration and job-search records remain in private runtime storage and are not listed in this public source repository. Discovery uses the scheduled, bounded worker and stores actual source observations privately. Posting visibility in an employer's current public ATS response is checked at discovery time; a posting timestamp can still be old, and the owner should verify availability and requirements before applying. Match scores are heuristic screening aids, not hiring predictions. The Google Sheet integration is separately authorized; private sync state and generated documents remain outside Git. AI-generated documents are disabled unless a dedicated production provider is configured.

Manual owner imports remain available separately. Restricted job sites and sites requiring login, CAPTCHA, or anti-bot workarounds are not automated.
