# Deployment

## Production boundary

BruteApply runs at `https://jobapply.brutehack.com` behind an exact-host Cloudflare Access application. Access allows only the owner identity. The frontend proxies same-origin `/api/*` requests to the private FastAPI service and forwards only the request headers needed for the API to validate the signed Cloudflare Access assertion and same-origin writes. The API independently checks signature, issuer, audience, expiry and owner email.

The production Compose project is `bruteapply-production`. PostgreSQL, Redis, API, worker, scheduler and frontend have no published host ports. The web/API/data networks are private. Only the public ATS discovery worker joins `bruteapply-production_discovery_egress`; it calls fixed HTTPS Greenhouse or Lever public-posting endpoints, disables redirects, does not follow job URLs, and discards postings outside the requested support-role/Canadian-region filter. No database, document, or credential service is attached to the egress-only network.

### Access JWT key refresh

The FastAPI API has no outbound network. It validates `Cf-Access-Jwt-Assertion` using the public Cloudflare Access JWKS cached in PostgreSQL. The egress-enabled general worker fetches only the fixed HTTPS `ACCESS_ISSUER/cdn-cgi/access/certs` endpoint and refreshes that cache every six hours; it stores only bounded, public RSA signing-key fields. The API fails closed when the cache is absent or older than 24 hours. `/readyz` includes cache freshness. Do not attach the API to an egress network to work around a stale cache; restore/refresh the worker path instead.

Production images use the full immutable source revision (`BRUTEAPPLY_REVISION`). Update the protected production environment's revision without printing its values, validate Compose, build the necessary image, and recreate only the changed services using the standard `bruteapply-production` project name. Never remove production volumes during deployment or rollback.

## Safe-ramp controls

- Canonical resume and provenance-linked facts live in private persistent storage; the source PDF is not in Git.
- Discovery is limited to explicitly configured, reviewed public ATS boards. It does not scrape restricted platforms or submit applications.
- Google Sheets is connected through the dedicated Sheets worker; its Sheets-only OAuth credential is mounted only there. Sync history, conflicts and row counts are available through the authenticated integration API.
- Production OpenAI, Gmail and Calendar remain disabled. Fake AI is test-only. Durable production AI budget accounting is not implemented.
- Real autonomous submission is disabled; the persisted emergency stop remains active. No real employer-submission adapter is configured.
- Production backups remain **DEFERRED / NOT IMPLEMENTED**.

## Validation

Validate with the documented locked backend test image, frontend lint/typecheck/production build, dependency audit, secret/privacy scans, Compose validation, and private service health checks. A public unauthenticated request must receive Cloudflare Access behavior, never the application dashboard. Owner-authenticated browser requests should return production API data; an invalid or missing Access assertion must continue to receive 401 from the API. If the dashboard reports unavailable private data, check API 401 counts and the JWKS cache freshness before changing any Access or network policy. The dashboard's Getting Started page reports live readiness and explicitly distinguishes public ATS discovery APIs from employer-authorized submission APIs.

## Rollback

For an application-only code regression, restore the previous immutable image tag and recreate only the affected BruteApply service. Do not remove PostgreSQL, Redis or private document volumes. For public-boundary failures, remove only the exact `jobapply.brutehack.com` route/record under the existing rollback procedure. Do not change other Access applications, routes, DNS, or zone TLS settings.
