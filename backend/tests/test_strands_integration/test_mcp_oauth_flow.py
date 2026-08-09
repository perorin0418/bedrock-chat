import asyncio
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, ".")
from app.strands_integration.tools.mcp_oauth_flow import (
    build_authorization_url,
    exchange_code_for_tokens,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthMetadata


def run(coro):
    return asyncio.run(coro)


class TestBuildAuthorizationUrl(unittest.TestCase):
    def test_includes_pkce_and_state(self):
        metadata = OAuthMetadata(
            issuer="https://auth.atlassian.com",
            authorization_endpoint="https://auth.atlassian.com/authorize",
            token_endpoint="https://auth.atlassian.com/oauth/token",
        )
        client_info = OAuthClientInformationFull(
            redirect_uris=["https://api.example.com/mcp/oauth/callback"],
            client_id="client-1",
        )

        url = build_authorization_url(
            metadata,
            client_info,
            redirect_uri="https://api.example.com/mcp/oauth/callback",
            code_challenge="challenge-1",
            state="state-1",
            scope="offline_access read:jira-work",
        )

        self.assertTrue(url.startswith("https://auth.atlassian.com/authorize?"))
        self.assertIn("code_challenge=challenge-1", url)
        self.assertIn("code_challenge_method=S256", url)
        self.assertIn("state=state-1", url)
        self.assertIn("response_type=code", url)
        self.assertIn("client_id=client-1", url)


class TestExchangeCodeForTokens(unittest.TestCase):
    @patch("httpx.AsyncClient")
    def test_posts_token_request_with_code_verifier(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "a1",
            "token_type": "Bearer",
            "refresh_token": "r1",
        }
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        metadata = OAuthMetadata(
            issuer="https://auth.atlassian.com",
            authorization_endpoint="https://auth.atlassian.com/authorize",
            token_endpoint="https://auth.atlassian.com/oauth/token",
        )
        client_info = OAuthClientInformationFull(
            redirect_uris=["https://api.example.com/mcp/oauth/callback"],
            client_id="client-1",
            client_secret="secret-1",
        )

        token = run(
            exchange_code_for_tokens(
                metadata,
                client_info,
                code="code-1",
                code_verifier="verifier-1",
                redirect_uri="https://api.example.com/mcp/oauth/callback",
            )
        )

        self.assertEqual(token.access_token, "a1")
        self.assertEqual(token.refresh_token, "r1")
        posted_data = mock_client.post.call_args.kwargs["data"]
        self.assertEqual(posted_data["grant_type"], "authorization_code")
        self.assertEqual(posted_data["code"], "code-1")
        self.assertEqual(posted_data["code_verifier"], "verifier-1")
        self.assertEqual(posted_data["client_id"], "client-1")
        self.assertEqual(posted_data["client_secret"], "secret-1")


if __name__ == "__main__":
    unittest.main()
