# Cloudflare + Render testing pilot (not deployed)

Approved hostname: `app.ccmedicalhs-inventory.com`. Owner approved public-facing Render hosting **only when login is mandatory**, behind Cloudflare Access and a signed-token origin gate. Approved testers: bcarlin@ccmedicalhs.com, sgascoine@ccmedicalhs.com, eric@ccmedicalhs.com. Use synthetic data only.

## Implemented in repository
- `cloudflare_access_gate.py` verifies the signed Cloudflare Access JWT against the team JWKS, RS256, issuer, audience, expiration, issued-at and exact allowlisted emails.
- Rejects missing/invalid tokens on **every route, including /login**. It also rejects direct requests to the Render origin without a valid signed Cloudflare token.
- Gate is opt-in via `CCM_CF_ACCESS_REQUIRED=1` and requires `CCM_PG_SECURE=1`; the existing Render startup guard remains active.
- Unit tests cover missing configuration, missing/invalid tokens, and signed-identity allowlisting. No real Cloudflare credentials in GitHub.

## Values to obtain privately in Cloudflare Zero Trust
- **Team domain:** Zero Trust settings team domain (e.g. `old-field-a7b1.cloudflareaccess.com`; verify exact spelling in dashboard).
- **Application AUD tag:** Access > Applications > app > Overview / Application configuration > Application Audience (AUD) tag. This is not the tunnel ID.
- **Identity provider:** One-time PIN or Microsoft Entra ID; only approved email addresses in the existing Allow policy.
- **DNS:** After a Render test service passes all prelaunch checks, add the `app` CNAME to the Render target with Cloudflare proxy ON. A Cloudflare Access application configuration alone does not create the DNS route.
- **Tunnel:** Existing tunnel is not required for this approach. Do not paste its token into GitHub, chat or Render.

## Required Render environment variables (secret manager only)
- `CCM_CF_ACCESS_REQUIRED=1`
- `CCM_CF_TEAM_DOMAIN=<verified-team>.cloudflareaccess.com`
- `CCM_CF_ACCESS_AUD=<Cloudflare application AUD>`
- `CCM_CF_ALLOWED_EMAILS=bcarlin@ccmedicalhs.com,sgascoine@ccmedicalhs.com,eric@ccmedicalhs.com`
- `CCM_PG_SECURE=1`, `CCM_PG_STAGING=1`, strong `CCM_SECRET`, distinct disposable `CCM_STAGING_DATABASE_URL`.
- HTTPS and trusted proxy settings require explicit verification before any service starts.

## Outstanding launch gates
1. Existing Render startup guard blocks all Render hosting. Do not remove until the synthetic-only hosting entrypoint, PostgreSQL schema initialization, strict authorization, and browser UI are verified.
2. Cloudflare Access signed-token verification must pass real integration checks through Cloudflare and the **direct Render URL** (direct URL without token must return 403). Do not rely on DNS alone.
3. Provision employee credentials securely, individually, outside chat/GitHub; verify login/logout, idle expiration, admin and employee roles, CSRF and rate limits.
4. Confirm managed database isolation and recoverability. Existing free database has an unrestricted IP allowlist and expiration; do not assume it is suitable for sensitive data.
5. Disable or securely implement file uploads and any legacy authentication bypasses. Review sensitive GET routes.
6. Keep TESTING — NOT LIVE banner and no real customer records or external integrations.

No web service, credentials, or DNS records have been created by these code changes.
