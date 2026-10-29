# Deployment

## Current status

Local synthetic demo source is implemented. Production deployment is **not activated**. Do not create DNS, Tunnel, Access, containers or production secrets from this template until the exact hostname has an owner-controlled Access boundary and protected runtime provisioning is ready.

## Required production components

1. Create an exact-host Cloudflare Access application for `jobapply.brutehack.com` and allow only the owner identity provisioned in protected runtime configuration. Do not add wildcard scope or edit another Access application.
2. Existing Cloudflare Tunnel and private Caddy routing, exact hostname only.
3. Isolated web/API, worker, PostgreSQL and Redis services on private Compose networks.
4. Read-only/non-root runtime where compatible, dropped capabilities, no-new-privileges, resource limits, health checks, no published application ports, no Docker socket.
5. PostgreSQL and Redis private volumes; documents outside the web root encrypted with AES-GCM.
6. Protected secret files for database credentials, document encryption key, and any separately approved OAuth/AI credentials.
7. Migrations run as a controlled one-shot task; app readiness must require database connectivity.

Production activation remains gated on Cloudflare Access, secret provisioning, database/worker validation, canonical resume onboarding, Google authorization, first-write Sheet snapshot, monitoring and recovery review. No autonomous submission is enabled by the deployment template.
