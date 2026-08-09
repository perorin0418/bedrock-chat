import asyncio
import json
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, ".")
from app.strands_integration.tools.mcp_oauth_storage import SecretsManagerTokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken


def run(coro):
    return asyncio.run(coro)


class TestSecretsManagerTokenStorage(unittest.TestCase):
    def test_get_tokens_returns_none_when_no_arn(self):
        storage = SecretsManagerTokenStorage(
            user_id="user-1", bot_id="bot-1", label="atlassian", oauth_secret_arn=None
        )
        self.assertIsNone(run(storage.get_tokens()))
        self.assertIsNone(run(storage.get_client_info()))

    @patch("app.strands_integration.tools.mcp_oauth_storage.get_api_key_from_secret_manager")
    def test_get_tokens_parses_blob(self, mock_get):
        mock_get.return_value = json.dumps(
            {"atlassian": {"tokens": {"access_token": "a1", "token_type": "Bearer"}}}
        )
        storage = SecretsManagerTokenStorage(
            user_id="user-1",
            bot_id="bot-1",
            label="atlassian",
            oauth_secret_arn="arn:aws:secretsmanager:...",
        )

        tokens = run(storage.get_tokens())

        self.assertIsInstance(tokens, OAuthToken)
        assert tokens is not None
        self.assertEqual(tokens.access_token, "a1")

    @patch("app.strands_integration.tools.mcp_oauth_storage.store_api_key_to_secret_manager")
    @patch("app.strands_integration.tools.mcp_oauth_storage.get_api_key_from_secret_manager")
    def test_set_tokens_creates_secret_when_none_and_updates_arn(
        self, mock_get, mock_store
    ):
        mock_get.side_effect = Exception("ResourceNotFoundException")
        mock_store.return_value = "arn:aws:secretsmanager:new-arn"
        storage = SecretsManagerTokenStorage(
            user_id="user-1", bot_id="bot-1", label="atlassian", oauth_secret_arn=None
        )

        run(storage.set_tokens(OAuthToken(access_token="a1", token_type="Bearer")))

        self.assertEqual(storage.oauth_secret_arn, "arn:aws:secretsmanager:new-arn")
        mock_store.assert_called_once()
        stored_blob = json.loads(mock_store.call_args.args[3])
        self.assertEqual(stored_blob["atlassian"]["tokens"]["access_token"], "a1")

    @patch("app.strands_integration.tools.mcp_oauth_storage.store_api_key_to_secret_manager")
    @patch("app.strands_integration.tools.mcp_oauth_storage.get_api_key_from_secret_manager")
    def test_set_client_info_preserves_other_labels(self, mock_get, mock_store):
        mock_get.return_value = json.dumps(
            {"other_label": {"client_info": {"client_id": "existing"}}}
        )
        mock_store.return_value = "arn:aws:secretsmanager:..."
        storage = SecretsManagerTokenStorage(
            user_id="user-1",
            bot_id="bot-1",
            label="atlassian",
            oauth_secret_arn="arn:aws:secretsmanager:...",
        )

        run(
            storage.set_client_info(
                OAuthClientInformationFull(
                    redirect_uris=["https://example.com/callback"],
                    client_id="new-client",
                )
            )
        )

        stored_blob = json.loads(mock_store.call_args.args[3])
        self.assertEqual(
            stored_blob["other_label"]["client_info"]["client_id"], "existing"
        )
        self.assertEqual(stored_blob["atlassian"]["client_info"]["client_id"], "new-client")


if __name__ == "__main__":
    unittest.main()
