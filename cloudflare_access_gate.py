"""Fail-closed Cloudflare Access JWT verification for an approved test pilot.

Cloudflare Access protects the custom hostname; this gate also rejects requests
to Render's public onrender.com origin unless they carry a valid signed token.
It is an additional control, not a replacement for employee login.
"""
import os
import jwt
from flask import abort, request


def register_cloudflare_access_gate(app):
    team = os.environ.get("CCM_CF_TEAM_DOMAIN", "").strip().lower()
    audience = os.environ.get("CCM_CF_ACCESS_AUD", "").strip()
    emails = {
        x.strip().lower()
        for x in os.environ.get("CCM_CF_ALLOWED_EMAILS", "").split(",")
        if x.strip()
    }
    if not team or not audience or not emails:
        raise RuntimeError("Cloudflare Access team domain, application AUD, and allowed emails are required")
    if not team.endswith(".cloudflareaccess.com") or "/" in team or ":" in team:
        raise RuntimeError("Invalid Cloudflare Access team domain")
    if not all("@" in email for email in emails):
        raise RuntimeError("Invalid approved email list")
    issuer = "https://" + team
    jwks = jwt.PyJWKClient(issuer + "/cdn-cgi/access/certs", cache_jwk_set=True, lifespan=300)

    @app.before_request
    def verify_cloudflare_access():
        # All endpoints, including /login, must be protected. Never trust a
        # client-supplied identity header without signature validation.
        token = request.headers.get("Cf-Access-Jwt-Assertion", "").strip()
        if not token:
            token = request.cookies.get("CF_Authorization", "")
        if not token:
            abort(403, "Cloudflare Access authentication required")
        try:
            key = jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token, key.key, algorithms=["RS256"], audience=audience,
                issuer=issuer, options={"require": ["exp", "iat", "aud", "iss", "sub"]},
                leeway=30,
            )
        except (jwt.PyJWTError, ValueError):
            abort(403, "Invalid Cloudflare Access authentication")
        if str(claims.get("email", "")).lower() not in emails:
            abort(403, "Employee not approved for this pilot")
