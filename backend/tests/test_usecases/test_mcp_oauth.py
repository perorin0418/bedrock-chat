import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, ".")
from app.usecases.mcp_oauth import (
    complete_mcp_oauth_callback,
    disconnect_mcp_oauth,
    start_mcp_oauth_authorize,
)
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


class TestCompleteMcpOauthCallback(unittest.TestCase):
    @patch(
        "app.usecases.mcp_oauth.MCP_OAUTH_REDIRECT_URI",
        "https://api.example.com/mcp/oauth/callback",
    )
    @patch("app.usecases.mcp_oauth.update_bot_mcp_oauth_secret_arn")
    @patch("app.usecases.mcp_oauth.SecretsManagerTokenStorage")
    @patch("app.usecases.mcp_oauth.exchange_code_for_tokens", new_callable=AsyncMock)
    @patch("app.usecases.mcp_oauth.discover_and_register", new_callable=AsyncMock)
    @patch("app.usecases.mcp_oauth.pop_mcp_oauth_state")
    @patch("app.usecases.mcp_oauth.find_bot_by_id")
    def test_completes_and_stores_tokens(
        self,
        mock_find_bot,
        mock_pop_state,
        mock_discover,
        mock_exchange,
        mock_storage_cls,
        mock_update_arn,
    ):
        from app.repositories.mcp_oauth_state import McpOAuthStateItem

        bot, tool = _make_bot()
        mock_find_bot.return_value = bot
        mock_pop_state.return_value = McpOAuthStateItem(
            bot_id="bot-1",
            label="atlassian",
            owner_user_id="user-1",
            code_verifier="verifier-1",
            client_id="client-1",
        )
        mock_discover.return_value = (
            OAuthMetadata(
                issuer="https://auth.atlassian.com",
                authorization_endpoint="https://auth.atlassian.com/authorize",
                token_endpoint="https://auth.atlassian.com/oauth/token",
            ),
            OAuthClientInformationFull(
                redirect_uris=["https://api.example.com/mcp/oauth/callback"],
                client_id="client-1",
            ),
        )
        from mcp.shared.auth import OAuthToken

        mock_exchange.return_value = OAuthToken(access_token="a1", token_type="Bearer")
        mock_storage = MagicMock()
        mock_storage.set_client_info = AsyncMock()
        mock_storage.set_tokens = AsyncMock()
        mock_storage.oauth_secret_arn = "arn:aws:secretsmanager:new-arn"
        mock_storage_cls.return_value = mock_storage

        import asyncio

        bot_id, label, succeeded = asyncio.run(
            complete_mcp_oauth_callback(code="code-1", state="state-1")
        )

        self.assertEqual(bot_id, "bot-1")
        self.assertEqual(label, "atlassian")
        self.assertTrue(succeeded)
        mock_storage.set_tokens.assert_called_once()
        mock_update_arn.assert_called_once_with(
            owner_user_id="user-1",
            bot_id="bot-1",
            tool_index=0,
            oauth_secret_arn="arn:aws:secretsmanager:new-arn",
        )

    @patch("app.usecases.mcp_oauth.pop_mcp_oauth_state")
    def test_returns_failure_when_state_unknown(self, mock_pop_state):
        mock_pop_state.return_value = None

        import asyncio

        with self.assertRaises(ValueError):
            asyncio.run(complete_mcp_oauth_callback(code="code-1", state="unknown"))

    @patch("app.usecases.mcp_oauth.discover_and_register", new_callable=AsyncMock)
    @patch("app.usecases.mcp_oauth.pop_mcp_oauth_state")
    @patch("app.usecases.mcp_oauth.find_bot_by_id")
    def test_returns_failure_when_unexpected_error_occurs_mid_flow(
        self, mock_find_bot, mock_pop_state, mock_discover
    ):
        """An exception raised anywhere after the state is popped (e.g. a
        network error during MCP server discovery, not just token exchange)
        must still yield a clean failure tuple instead of propagating out of
        this unauthenticated callback endpoint."""
        from app.repositories.mcp_oauth_state import McpOAuthStateItem

        bot, _ = _make_bot()
        mock_find_bot.return_value = bot
        mock_pop_state.return_value = McpOAuthStateItem(
            bot_id="bot-1",
            label="atlassian",
            owner_user_id="user-1",
            code_verifier="verifier-1",
            client_id="client-1",
        )
        mock_discover.side_effect = RuntimeError("network boom")

        import asyncio

        with self.assertLogs("app.usecases.mcp_oauth", level="ERROR") as logs:
            bot_id, label, succeeded = asyncio.run(
                complete_mcp_oauth_callback(code="code-1", state="state-1")
            )

        self.assertEqual(bot_id, "bot-1")
        self.assertEqual(label, "atlassian")
        self.assertFalse(succeeded)
        self.assertTrue(any("bot-1" in message for message in logs.output))
        self.assertTrue(any("atlassian" in message for message in logs.output))
        # Never leak the underlying exception message content is fine to log
        # server-side, but the point of this test is only that we DID log
        # and DID NOT propagate.

    @patch("app.usecases.mcp_oauth.find_bot_by_id")
    @patch("app.usecases.mcp_oauth.pop_mcp_oauth_state")
    def test_returns_failure_when_find_bot_by_id_raises(
        self, mock_pop_state, mock_find_bot
    ):
        from app.repositories.mcp_oauth_state import McpOAuthStateItem

        mock_pop_state.return_value = McpOAuthStateItem(
            bot_id="bot-1",
            label="atlassian",
            owner_user_id="user-1",
            code_verifier="verifier-1",
            client_id="client-1",
        )
        mock_find_bot.side_effect = Exception("bot not found")

        import asyncio

        with self.assertLogs("app.usecases.mcp_oauth", level="ERROR"):
            bot_id, label, succeeded = asyncio.run(
                complete_mcp_oauth_callback(code="code-1", state="state-1")
            )

        self.assertEqual(bot_id, "bot-1")
        self.assertEqual(label, "atlassian")
        self.assertFalse(succeeded)


class TestDisconnectMcpOauth(unittest.TestCase):
    @patch("app.usecases.mcp_oauth.store_api_key_to_secret_manager")
    @patch("app.usecases.mcp_oauth.get_api_key_from_secret_manager")
    @patch("app.usecases.mcp_oauth.find_bot_by_id")
    def test_removes_only_target_label(self, mock_find_bot, mock_get, mock_store):
        import json

        bot, tool = _make_bot(oauth_secret_arn="arn:aws:secretsmanager:...")
        mock_find_bot.return_value = bot
        mock_get.return_value = json.dumps(
            {
                "atlassian": {"tokens": {"access_token": "a1"}},
                "other_label": {"tokens": {"access_token": "a2"}},
            }
        )
        user = User(id="user-1", name="n", email="e", groups=[])

        disconnect_mcp_oauth(user, "bot-1", "atlassian")

        stored_blob = json.loads(mock_store.call_args.args[3])
        self.assertNotIn("atlassian", stored_blob)
        self.assertIn("other_label", stored_blob)

    @patch("app.usecases.mcp_oauth.find_bot_by_id")
    def test_raises_when_not_owner(self, mock_find_bot):
        bot, _ = _make_bot(oauth_secret_arn="arn:aws:secretsmanager:...")
        bot.is_owned_by_user.return_value = False
        mock_find_bot.return_value = bot
        user = User(id="other-user", name="n", email="e", groups=[])

        with self.assertRaises(PermissionError):
            disconnect_mcp_oauth(user, "bot-1", "atlassian")

    @patch("app.usecases.mcp_oauth.find_bot_by_id")
    def test_noop_when_never_connected(self, mock_find_bot):
        bot, _ = _make_bot(oauth_secret_arn=None)
        mock_find_bot.return_value = bot
        user = User(id="user-1", name="n", email="e", groups=[])

        disconnect_mcp_oauth(user, "bot-1", "atlassian")  # must not raise


if __name__ == "__main__":
    unittest.main()
