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
