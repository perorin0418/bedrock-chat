import sys

sys.path.append(".")
import time
import unittest
from unittest.mock import MagicMock, patch

from app.strands_integration.tools.mcp_tools import _token_cache, get_mcp_bearer_token


class TestGetMcpBearerToken(unittest.TestCase):
    def setUp(self):
        _token_cache.clear()

    @patch("app.strands_integration.tools.mcp_tools.requests.post")
    def test_fetches_and_caches_token(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "token-1",
            "expires_in": 3600,
        }
        mock_post.return_value = mock_response

        token = get_mcp_bearer_token("client-1", "secret-1", "example.auth.region.amazoncognito.com", "cache-key-1")

        self.assertEqual(token, "token-1")
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(
            args[0],
            "https://example.auth.region.amazoncognito.com/oauth2/token",
        )
        self.assertEqual(kwargs["auth"], ("client-1", "secret-1"))
        self.assertEqual(
            kwargs["data"],
            {"grant_type": "client_credentials", "scope": "knowledge-mcp/invoke"},
        )

    @patch("app.strands_integration.tools.mcp_tools.requests.post")
    def test_returns_cached_token_without_refetch(self, mock_post):
        _token_cache["cache-key-2"] = ("cached-token", time.time() + 3600)

        token = get_mcp_bearer_token("client-1", "secret-1", "example.auth.region.amazoncognito.com", "cache-key-2")

        self.assertEqual(token, "cached-token")
        mock_post.assert_not_called()

    @patch("app.strands_integration.tools.mcp_tools.requests.post")
    def test_refetches_when_near_expiry(self, mock_post):
        _token_cache["cache-key-3"] = ("stale-token", time.time() + 30)
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "fresh-token",
            "expires_in": 3600,
        }
        mock_post.return_value = mock_response

        token = get_mcp_bearer_token("client-1", "secret-1", "example.auth.region.amazoncognito.com", "cache-key-3")

        self.assertEqual(token, "fresh-token")
        mock_post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
