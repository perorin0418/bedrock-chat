import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, ".")
from app.usecases.mcp_oauth import start_mcp_oauth_authorize
from app.user import User
from mcp.shared.auth import OAuthClientInformationFull, OAuthMetadata


def _make_bot(auth_type="oauth", oauth_secret_arn=None):
    bot = MagicMock()
    bot.id = "bot-1"
    bot.owner_user_id = "user-1"
    bot.is_owned_by_user.return_value = True
    server = MagicMock()
    server.label = "atlassian"
    server.endpoint_url = "https://mcp.atlassian.com/v1/mcp"
    server.auth_type = auth_type
    tool = MagicMock()
    tool.tool_type = "mcp"
    tool.mcpServers = [server]
    tool.oauth_secret_arn = oauth_secret_arn
    bot.agent.tools = [tool]
    return bot, tool


class TestStartMcpOauthAuthorize(unittest.TestCase):
    @patch("app.usecases.mcp_oauth.save_mcp_oauth_state")
    @patch("app.usecases.mcp_oauth.build_authorization_url")
    @patch("app.usecases.mcp_oauth.discover_and_register")
    @patch("app.usecases.mcp_oauth.find_bot_by_id")
    def test_raises_when_not_owner(
        self, mock_find_bot, mock_discover, mock_build_url, mock_save_state
    ):
        bot, _ = _make_bot()
        bot.is_owned_by_user.return_value = False
        mock_find_bot.return_value = bot
        user = User(id="other-user", name="n", email="e", groups=[])

        with self.assertRaises(PermissionError):
            import asyncio

            asyncio.run(start_mcp_oauth_authorize(user, "bot-1", "atlassian"))

    @patch("app.usecases.mcp_oauth.save_mcp_oauth_state")
    @patch("app.usecases.mcp_oauth.build_authorization_url")
    @patch("app.usecases.mcp_oauth.discover_and_register", new_callable=AsyncMock)
    @patch("app.usecases.mcp_oauth.find_bot_by_id")
    def test_returns_authorization_url(
        self, mock_find_bot, mock_discover, mock_build_url, mock_save_state
    ):
        bot, _ = _make_bot()
        mock_find_bot.return_value = bot
        metadata = OAuthMetadata(
            issuer="https://auth.atlassian.com",
            authorization_endpoint="https://auth.atlassian.com/authorize",
            token_endpoint="https://auth.atlassian.com/oauth/token",
        )
        client_info = OAuthClientInformationFull(
            redirect_uris=["https://api.example.com/mcp/oauth/callback"],
            client_id="client-1",
        )
        mock_discover.return_value = (metadata, client_info)
        mock_build_url.return_value = "https://auth.atlassian.com/authorize?..."
        user = User(id="user-1", name="n", email="e", groups=[])

        import asyncio

        url = asyncio.run(start_mcp_oauth_authorize(user, "bot-1", "atlassian"))

        self.assertEqual(url, "https://auth.atlassian.com/authorize?...")
        mock_discover.assert_called_once()
        mock_save_state.assert_called_once()
        saved_kwargs = mock_save_state.call_args.kwargs
        self.assertEqual(saved_kwargs["bot_id"], "bot-1")
        self.assertEqual(saved_kwargs["label"], "atlassian")
        self.assertEqual(saved_kwargs["owner_user_id"], "user-1")
        self.assertEqual(saved_kwargs["client_id"], "client-1")

    @patch("app.usecases.mcp_oauth.find_bot_by_id")
    def test_raises_when_label_not_oauth(self, mock_find_bot):
        bot, tool = _make_bot(auth_type="bearer_token")
        mock_find_bot.return_value = bot
        user = User(id="user-1", name="n", email="e", groups=[])

        import asyncio

        with self.assertRaises(ValueError):
            asyncio.run(start_mcp_oauth_authorize(user, "bot-1", "atlassian"))


if __name__ == "__main__":
    unittest.main()
