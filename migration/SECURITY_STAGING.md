# PostgreSQL security staging notes (not a deployment guide)

The development branch now includes an **opt-in** PostgreSQL employee authentication layer. It is **not enabled by default**. Existing SQLite prototype behavior remains unchanged.

To exercise secured PostgreSQL staging in an isolated local environment:
1. Use a disposable PostgreSQL database with `migration/staging_schema.sql`, `migration/product_schema.sql` and `migration/employee_schema.sql` applied.
2. Set `CCM_PG_STAGING=1`, `CCM_PG_SECURE=1`, `CCM_STAGING_DATABASE_URL` and a unique strong `CCM_SECRET`.
3. Set `CCM_BOOTSTRAP_EMAIL` and a 16+ character `CCM_BOOTSTRAP_PASSWORD`, then run `flask --app app create-pg-admin`. Remove bootstrap credentials immediately.
4. Start locally, not on a public network. The Flask Render startup guard intentionally remains in place.

Security features implemented: PostgreSQL-backed password hashes, account active checks, role-based write permissions, session renewal on login, CSRF validation on allowed POSTs, HTML form token injection, and basic response security headers. The viewer role is read-only.

**Still required before production:** complete all GET/POST application workflows, full CSRF/permission review, login rate limiting and lockouts, account provisioning and reset flows, HTTPS and trusted proxy configuration, audit logging, database backup/restore rehearsal, secrets management, and an explicit authorized deployment review. The Render database is **not** migrated by these CI tests, and no public web service has been deployed.

CI uses only disposable PostgreSQL 18. Do not run the destructive integration suite against Render.

## Private repair attachments (staging only)

The secured PostgreSQL application exposes authenticated upload and download routes at
`POST /repairs/<repair_id>/attachments` and
`GET /repairs/<repair_id>/attachments/<attachment_id>`.
Uploads require admin or employee role and a valid CSRF token; downloads require
an authenticated account. File types are restricted to JPEG, PNG and PDF by
basic header signature checks; files are limited to 5 MiB each, while HTTP
request bodies are capped at 10 MiB. Original filenames are metadata only.
Storage uses unpredictable names in a private, non-static directory with
restrictive filesystem permissions. Database metadata records SHA-256 and uploader.

Set `CCM_PRIVATE_UPLOAD_DIR` to a private persistent directory for local tests.
The default `/tmp/ccmedical-private-staging` is ephemeral and must **not** be
used for durable files. These endpoints are API routes; attachment upload
buttons and galleries have not yet been added to the employee interface.

**Not production-ready:** Signature checks are not malware scanning or
complete format validation. Before enabling real customer uploads, implement
antivirus scanning and content decoding, encrypted durable private storage,
storage lifecycle/retention, quotas, backup/restore, per-customer access
controls, and an operational upload UI. No public Render deployment is allowed.

## Employee activity history

PostgreSQL staging now records inventory receiving, location transfers, unit repair creation, customer repair updates, core stage changes, QC decisions, core cost transactions, core conversion, and customer repair attachment uploads. Entries are written in the same database transaction as the corresponding change so failed operations do not leave success events. In secured mode, entries include the authenticated employee ID; unsecured staging tests record a null actor. Audit details are deliberately limited to non-sensitive identifiers, statuses and locations; notes, passwords and file contents are not copied into the audit history.

This is an initial database audit trail, not a production-grade immutable ledger. The history is not yet displayed in the employee interface; access-controlled reporting, tamper resistance, retention, product database change coverage and administrative activity review remain open.
