import importlib.util
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# get_full_scope_oauth_token.py is a standalone script (not part of the
# `app` package), so load it via importlib under a private module name to
# avoid any risk of colliding with another same-named module elsewhere in
# the test suite (same defensive pattern used for the Lambda modules).
_MODULE_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "..",
    "scripts",
    "get_full_scope_oauth_token.py",
)
_spec = importlib.util.spec_from_file_location(
    "get_full_scope_oauth_token_module", _MODULE_PATH
)
assert _spec is not None and _spec.loader is not None
oauth_script = importlib.util.module_from_spec(_spec)
sys.modules["get_full_scope_oauth_token_module"] = oauth_script
_spec.loader.exec_module(oauth_script)


class TestGeneratePkce(unittest.TestCase):
    def test_generates_verifier_and_challenge_of_expected_length(self):
        verifier, challenge = oauth_script._generate_pkce()
        # base64url of 32 random bytes, no padding -> 43 chars.
        self.assertEqual(len(verifier), 43)
        self.assertEqual(len(challenge), 43)
        self.assertNotIn("=", verifier)
        self.assertNotIn("=", challenge)

    def test_challenge_is_sha256_of_verifier(self):
        import base64
        import hashlib

        verifier, challenge = oauth_script._generate_pkce()
        expected_challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        self.assertEqual(challenge, expected_challenge)

    def test_generates_different_values_each_call(self):
        v1, _ = oauth_script._generate_pkce()
        v2, _ = oauth_script._generate_pkce()
        self.assertNotEqual(v1, v2)


class TestBuildAuthorizeUrl(unittest.TestCase):
    def test_requests_full_scope_including_user_profile(self):
        url = oauth_script._build_authorize_url("challenge123", "state123")
        # user:profile is the scope Anthropic's /api/oauth/usage endpoint
        # requires; plain `claude setup-token` omits it, which is exactly
        # the gap this script exists to close.
        self.assertIn("user%3Aprofile", url)
        self.assertIn("user%3Ainference", url)
        self.assertIn("org%3Acreate_api_key", url)

    def test_uses_pkce_s256_and_copy_paste_mode(self):
        url = oauth_script._build_authorize_url("challenge123", "state123")
        self.assertIn("code_challenge=challenge123", url)
        self.assertIn("code_challenge_method=S256", url)
        self.assertIn("code=true", url)
        self.assertIn("state=state123", url)

    def test_targets_expected_authorize_host(self):
        url = oauth_script._build_authorize_url("c", "s")
        self.assertTrue(url.startswith("https://claude.ai/oauth/authorize?"))


class TestExchangeCodeForToken(unittest.TestCase):
    @patch("get_full_scope_oauth_token_module.urllib.request.urlopen")
    def test_sends_expected_token_request_body(self, mock_urlopen):
        import json

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {"access_token": "sk-ant-oat01-full", "scope": "user:profile user:inference"}
        ).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = oauth_script._exchange_code_for_token(
            code="auth-code",
            state="the-state",
            verifier="the-verifier",
            expires_in=31536000,
        )

        self.assertEqual(result["access_token"], "sk-ant-oat01-full")
        request = mock_urlopen.call_args[0][0]
        self.assertEqual(request.full_url, oauth_script.TOKEN_URL)
        # console.anthropic.com is behind Cloudflare, which blocks the
        # default `Python-urllib/x.y` User-Agent outright with a generic
        # "error code: 1010" before the request ever reaches Anthropic's
        # own API logic -- a browser-like User-Agent must be sent.
        self.assertIn("Chrome", request.get_header("User-agent"))
        sent_body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(sent_body["grant_type"], "authorization_code")
        self.assertEqual(sent_body["code"], "auth-code")
        self.assertEqual(sent_body["code_verifier"], "the-verifier")
        # Requests a long-lived token, matching Claude Code's own
        # long-lived setup-token behavior.
        self.assertEqual(sent_body["expires_in"], 31536000)

    @patch("get_full_scope_oauth_token_module.urllib.request.urlopen")
    def test_raises_with_response_body_on_http_error(self, mock_urlopen):
        import urllib.error

        mock_urlopen.side_effect = urllib.error.HTTPError(
            url=oauth_script.TOKEN_URL,
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=MagicMock(read=lambda: b'{"error":"invalid_grant"}'),
        )

        with self.assertRaises(RuntimeError) as ctx:
            oauth_script._exchange_code_for_token(
                code="bad-code", state="s", verifier="v", expires_in=3600
            )
        self.assertIn("400", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
