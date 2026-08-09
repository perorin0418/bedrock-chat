import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")
from app.repositories.mcp_oauth_state import (
    pop_mcp_oauth_state,
    save_mcp_oauth_state,
)


class TestMcpOauthStateRepository(unittest.TestCase):
    def setUp(self):
        self.patcher1 = patch("boto3.resource")
        self.mock_boto3_resource = self.patcher1.start()
        self.mock_table = MagicMock()
        self.mock_boto3_resource.return_value.Table.return_value = self.mock_table

        os.environ["MCP_OAUTH_STATE_TABLE_NAME"] = "test-mcp-oauth-state-table"
        os.environ["BEDROCK_REGION"] = "us-east-1"

    def tearDown(self):
        self.patcher1.stop()
        os.environ.pop("MCP_OAUTH_STATE_TABLE_NAME", None)
        os.environ.pop("BEDROCK_REGION", None)

    @patch(
        "app.repositories.mcp_oauth_state.get_current_time",
        return_value=1_700_000_000_000,
    )
    def test_save_mcp_oauth_state_puts_expected_item(self, mock_get_current_time):
        save_mcp_oauth_state(
            state="state-1",
            bot_id="bot-1",
            label="atlassian",
            owner_user_id="user-1",
            code_verifier="verifier-1",
            client_id="client-1",
            ttl_seconds=600,
        )

        self.mock_table.put_item.assert_called_once()
        item = self.mock_table.put_item.call_args.kwargs["Item"]
        self.assertEqual(item["State"], "state-1")
        self.assertEqual(item["BotId"], "bot-1")
        self.assertEqual(item["Label"], "atlassian")
        self.assertEqual(item["OwnerUserId"], "user-1")
        self.assertEqual(item["CodeVerifier"], "verifier-1")
        self.assertEqual(item["ClientId"], "client-1")
        self.assertEqual(item["expire"], 1_700_000_000 + 600)

    def test_pop_mcp_oauth_state_returns_item_and_deletes(self):
        self.mock_table.get_item.return_value = {
            "Item": {
                "State": "state-1",
                "BotId": "bot-1",
                "Label": "atlassian",
                "OwnerUserId": "user-1",
                "CodeVerifier": "verifier-1",
                "ClientId": "client-1",
            }
        }

        item = pop_mcp_oauth_state("state-1")

        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.bot_id, "bot-1")
        self.assertEqual(item.label, "atlassian")
        self.assertEqual(item.owner_user_id, "user-1")
        self.assertEqual(item.code_verifier, "verifier-1")
        self.assertEqual(item.client_id, "client-1")
        self.mock_table.delete_item.assert_called_once_with(Key={"State": "state-1"})

    def test_pop_mcp_oauth_state_returns_none_when_missing(self):
        self.mock_table.get_item.return_value = {}

        item = pop_mcp_oauth_state("missing-state")

        self.assertIsNone(item)
        self.mock_table.delete_item.assert_not_called()

    @patch(
        "app.repositories.mcp_oauth_state.get_current_time",
        return_value=1_700_000_000_000,
    )
    def test_pop_mcp_oauth_state_returns_none_when_already_expired(
        self, mock_get_current_time
    ):
        # DynamoDB TTL deletion is best-effort/eventual and can lag by hours,
        # so an item past its `expire` time but not yet TTL-deleted must
        # still be treated as missing (and cleaned up here).
        self.mock_table.get_item.return_value = {
            "Item": {
                "State": "state-1",
                "BotId": "bot-1",
                "Label": "atlassian",
                "OwnerUserId": "user-1",
                "CodeVerifier": "verifier-1",
                "ClientId": "client-1",
                "expire": 1_700_000_000 - 1,
            }
        }

        item = pop_mcp_oauth_state("state-1")

        self.assertIsNone(item)
        self.mock_table.delete_item.assert_called_once_with(Key={"State": "state-1"})


if __name__ == "__main__":
    unittest.main()
