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

Production images are tagged with the full 40-character source revision (`BRUTEAPPLY_REVISION`); record each resulting image ID/digest with the deployment. Discovery remains disabled by database policy, and the initial worker has no external-egress network. After an owner enables a reviewed public source, add only the required worker egress path as a reviewed deployment change.

## Owner-encrypted resume bootstrap

`backend/scripts/import_private_resume.py` is a one-shot private bootstrap task. It validates the owner-provided encrypted canonical and superseded resume manifests, decrypts only in process memory, checks the canonical SHA-256/page count and exact 104 page/section fact matches against a fresh PDF extraction, and re-encrypts the documents and each fact under fresh IDs into the private document volume. Normal resume upload/API behavior is unchanged. Sensitive/contact facts stay in `NEEDS_REVIEW`; ordinary verbatim facts can be `RESUME_VERIFIED`, which records document evidence but is not owner approval. The importer is idempotent and fails closed on a checksum, source, page, or provenance mismatch. It prints aggregate counts only.

For local Docker Compose file-backed secrets, keep the host secret directory owner-only and the individual source files owner/group readable only (`0640`, no group write or other access). Only services that need these mounted files receive the owner's private supplementary group. The API, worker, migration, database, and one-shot bootstrap use the secret mount; the frontend and scheduler do not. The document volume is created with UID/GID `10001:10001` ownership in the backend image so the non-root API/worker can maintain its `0700` root and `0600` encrypted files without a privileged runtime container.

Production activation remains gated on Cloudflare Access, secret provisioning, database/worker validation, canonical resume onboarding, Google authorization, first-write Sheet snapshot, monitoring and recovery review. No autonomous submission is enabled by the deployment template.
