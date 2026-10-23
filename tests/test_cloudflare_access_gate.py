"""Offline Cloudflare gate regression tests: no network or Render secrets."""
import os
import unittest
from unittest.mock import patch, MagicMock
from flask import Flask
from cloudflare_access_gate import register_cloudflare_access_gate

SETTINGS = {
    "CCM_CF_TEAM_DOMAIN": "example.cloudflareaccess.com",
    "CCM_CF_ACCESS_AUD": "synthetic-audience",
    "CCM_CF_ALLOWED_EMAILS": "bcarlin@example.test,eric@example.test",
}

class CloudflareAccessGateTests(unittest.TestCase):
    def make_app(self):
        app = Flask(__name__)
        app.add_url_rule("/", "index", lambda: "ok")
        register_cloudflare_access_gate(app)
        return app.test_client()

    def test_missing_configuration_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                self.make_app()

    def test_missing_and_invalid_token_denied(self):
        with patch.dict(os.environ, SETTINGS, clear=True):
            client = self.make_app()
            self.assertEqual(client.get("/").status_code, 403)
            self.assertEqual(client.get("/", headers={"Cf-Access-Jwt-Assertion": "invalid"}).status_code, 403)

    def test_signed_approved_identity_only(self):
        with patch.dict(os.environ, SETTINGS, clear=True):
            with patch("cloudflare_access_gate.jwt.PyJWKClient") as jwks_class:
                jwks_class.return_value.get_signing_key_from_jwt.return_value.key = "test-key"
                with patch("cloudflare_access_gate.jwt.decode") as decode:
                    client = self.make_app()
                    decode.return_value = {"email": "bcarlin@example.test"}
                    self.assertEqual(client.get("/", headers={"Cf-Access-Jwt-Assertion": "signed"}).status_code, 200)
                    decode.return_value = {"email": "outsider@example.test"}
                    self.assertEqual(client.get("/", headers={"Cf-Access-Jwt-Assertion": "signed"}).status_code, 403)
                    decode.assert_called_with(
                        "signed", "test-key", algorithms=["RS256"],
                        audience="synthetic-audience",
                        issuer="https://example.cloudflareaccess.com",
                        options={"require": ["exp", "iat", "aud", "iss", "sub"]},
                        leeway=30,
                    )

if __name__ == "__main__":
    unittest.main()
