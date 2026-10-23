# CC Medical — Authorized Private Testing Pilot

Status: **Owner authorized preparation of a private, password-protected Render test deployment on 2026-10-22. No hosted deployment has yet occurred.**

## Pilot scope
- One owner/admin tester initially. Additional employee accounts only after explicit selection and provisioning approval.
- Synthetic inventory, sample ultrasound equipment and fictitious customers only.
- No QuickBooks, Salesforce, or production inventory/customer data connections.
- Distinct testing database, credentials, uploads, logs and backups.
- Clear **TESTING — NOT LIVE** banner and a repeatable reset/seed procedure.
- Keep the existing Render startup block until all access-control gates below are demonstrably satisfied.

## Required gates before any hosted endpoint is enabled
1. Connect the owner's Render account and inspect existing services without changing them. Confirm account/team, region, service cost and test-only service name with owner.
2. Verify that access is **private at the network/edge layer** (identity-aware access or VPN/allowlist) *before* Flask login; a public Render URL with only an application login does not meet this requirement.
3. Configure HTTPS and trusted proxy handling safely. Do not trust arbitrary forwarded headers. Validate hostnames and secure session cookies; verify redirect and HSTS behavior in staging.
4. Create a new disposable PostgreSQL database, separate from the existing Render database. Apply staging/product/employee schemas once using a non-destructive migration process; never run destructive CI tests against Render.
5. Provision strong app secrets through Render secret storage. Do not store secrets or real passwords in GitHub, issues, logs, or chat. Require a unique owner account and MFA at the edge provider.
6. Review the legacy product-admin login path and all sensitive GET endpoints; deny access by default. Verify write roles, CSRF, audit attribution, rate limiting and account deactivation through browser tests.
7. Keep attachments disabled until private durable storage, malware scanning, authorization, retention and recovery are validated. Do not rely on ephemeral /tmp.
8. Verify managed PostgreSQL backup/restore, service logs and incident rollback. Confirm costs, data retention and shutdown plan.
9. Run end-to-end synthetic smoke tests for login/logout, receiving CC/CR/RP, transfers, repairs, QC, core conversion, costs and permissions.
10. Only after these gates pass, provide the owner with the restricted test URL and separately provisioned credentials. Obtain additional approval before inviting employees or using customer data.

## Current verified checks
- Automated offline, PostgreSQL integration, and disposable backup/restore jobs passed in GitHub Actions run 39848440609.
- Authenticated synthetic receiving and transfer were tested in the Flask test client.
- This does not constitute a deployed private site or independent security certification.

## Rollback
Disable edge access and stop the test service, revoke secrets, preserve logs as authorized, and delete only explicitly identified disposable pilot resources after owner approval. Never modify the production environment or existing isolated Render database without separate approval.
