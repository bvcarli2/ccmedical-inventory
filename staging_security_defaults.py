"""Shared deployment-independent security defaults for opt-in PostgreSQL staging.

These defaults do not authorize a public deployment.
"""
def configure_staging_limits(app):
    app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

def apply_staging_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response
