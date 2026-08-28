import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")
from app.claude_teams.token_repository import (
    create_token,
    delete_token,
    disable_token,
    get_token,
    list_tokens,
    pick_next_available_token,
    set_cooldown,
    set_enabled,
)


class TestClaudeTeamsTokenRepository(unittest.TestCase):
    def setUp(self):
        self.patcher = patch("boto3.resource")
        self.mock_boto3_resource = self.patcher.start()
        self.mock_table = MagicMock()
        self.mock_boto3_resource.return_value.Table.return_value = self.mock_table

        os.environ["CLAUDE_TEAMS_TOKEN_TABLE_NAME"] = "test-claude-teams-token-table"
        os.environ["BEDROCK_REGION"] = "us-east-1"

    def tearDown(self):
        self.patcher.stop()
        os.environ.pop("CLAUDE_TEAMS_TOKEN_TABLE_NAME", None)
        os.environ.pop("BEDROCK_REGION", None)

    @patch(
        "app.claude_teams.token_repository.get_current_time",
        return_value=1_700_000_000_000,
    )
    def test_create_token_puts_expected_item(self, mock_time):
        item = create_token(display_name="team-a-token")

        self.mock_table.put_item.assert_called_once()
        put_item = self.mock_table.put_item.call_args.kwargs["Item"]
        self.assertEqual(put_item["DisplayName"], "team-a-token")
        self.assertTrue(put_item["Enabled"])
        self.assertEqual(put_item["CreatedAt"], 1_700_000_000_000)
        self.assertIsNone(put_item.get("CooldownUntil"))
        self.assertEqual(item.display_name, "team-a-token")
        self.assertTrue(item.enabled)

    def test_create_token_mints_an_ingest_secret(self):
        item = create_token(display_name="team-a-token")

        self.assertIsNotNone(item.ingest_secret)
        put_item = self.mock_table.put_item.call_args.kwargs["Item"]
        self.assertEqual(put_item["IngestSecret"], item.ingest_secret)

    def test_create_token_mints_distinct_secrets_for_different_tokens(self):
        first = create_token(display_name="a")
        second = create_token(display_name="b")

        self.assertNotEqual(first.ingest_secret, second.ingest_secret)

    def test_list_tokens_returns_items_from_scan(self):
        self.mock_table.scan.return_value = {
            "Items": [
                {
                    "TokenId": "tok-1",
                    "DisplayName": "team-a",
                    "Enabled": True,
                    "CreatedAt": 1_700_000_000_000,
                }
            ]
        }

        tokens = list_tokens()

        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0].token_id, "tok-1")
        self.assertEqual(tokens[0].display_name, "team-a")
        self.assertTrue(tokens[0].enabled)
        self.assertIsNone(tokens[0].cooldown_until)
        self.assertIsNone(tokens[0].last_used_at)

    def test_list_tokens_paginates(self):
        self.mock_table.scan.side_effect = [
            {
                "Items": [{"TokenId": "tok-1", "DisplayName": "a", "Enabled": True, "CreatedAt": 1}],
                "LastEvaluatedKey": {"TokenId": "tok-1"},
            },
            {
                "Items": [{"TokenId": "tok-2", "DisplayName": "b", "Enabled": True, "CreatedAt": 2}],
            },
        ]

        tokens = list_tokens()

        self.assertEqual([t.token_id for t in tokens], ["tok-1", "tok-2"])
        self.assertEqual(self.mock_table.scan.call_count, 2)

    def test_set_enabled_updates_item(self):
        set_enabled("tok-1", False)

        self.mock_table.update_item.assert_called_once()
        kwargs = self.mock_table.update_item.call_args.kwargs
        self.assertEqual(kwargs["Key"], {"TokenId": "tok-1"})
        self.assertEqual(
            kwargs["ExpressionAttributeValues"][":enabled"], False
        )

    def test_disable_token_calls_set_enabled_false(self):
        disable_token("tok-1")

        self.mock_table.update_item.assert_called_once()
        kwargs = self.mock_table.update_item.call_args.kwargs
        self.assertEqual(kwargs["ExpressionAttributeValues"][":enabled"], False)

    @patch(
        "app.claude_teams.token_repository.get_current_time",
        return_value=1_700_000_000_000,
    )
    def test_set_cooldown_updates_item(self, mock_time):
        set_cooldown("tok-1", cooldown_until_epoch_seconds=1_700_000_300)

        self.mock_table.update_item.assert_called_once()
        kwargs = self.mock_table.update_item.call_args.kwargs
        self.assertEqual(kwargs["Key"], {"TokenId": "tok-1"})
        self.assertEqual(
            kwargs["ExpressionAttributeValues"][":cooldown_until"], 1_700_000_300
        )

    def test_delete_token_deletes_item(self):
        delete_token("tok-1")

        self.mock_table.delete_item.assert_called_once_with(Key={"TokenId": "tok-1"})

    @patch(
        "app.claude_teams.token_repository.get_current_time",
        return_value=1_700_000_000_000,
    )
    def test_pick_next_available_token_skips_disabled_and_cooling_down(
        self, mock_time
    ):
        self.mock_table.scan.return_value = {
            "Items": [
                {
                    "TokenId": "tok-disabled",
                    "DisplayName": "disabled",
                    "Enabled": False,
                    "CreatedAt": 1,
                    "LastUsedAt": 100,
                },
                {
                    "TokenId": "tok-cooling",
                    "DisplayName": "cooling",
                    "Enabled": True,
                    "CreatedAt": 1,
                    "LastUsedAt": 50,
                    "CooldownUntil": 1_700_000_500,  # future
                },
                {
                    "TokenId": "tok-available-older",
                    "DisplayName": "available-older",
                    "Enabled": True,
                    "CreatedAt": 1,
                    "LastUsedAt": 10,
                },
                {
                    "TokenId": "tok-available-newer",
                    "DisplayName": "available-newer",
                    "Enabled": True,
                    "CreatedAt": 1,
                    "LastUsedAt": 900,
                },
            ]
        }

        picked = pick_next_available_token()

        self.assertIsNotNone(picked)
        assert picked is not None
        # Round robin: pick the one least-recently used among available candidates.
        self.assertEqual(picked.token_id, "tok-available-older")
        self.mock_table.update_item.assert_called_once()
        update_kwargs = self.mock_table.update_item.call_args.kwargs
        self.assertEqual(update_kwargs["Key"], {"TokenId": "tok-available-older"})
        self.assertEqual(
            update_kwargs["ExpressionAttributeValues"][":last_used_at"],
            1_700_000_000_000,
        )

    def test_pick_next_available_token_treats_expired_cooldown_as_available(self):
        self.mock_table.scan.return_value = {
            "Items": [
                {
                    "TokenId": "tok-1",
                    "DisplayName": "a",
                    "Enabled": True,
                    "CreatedAt": 1,
                    "CooldownUntil": 1,  # long past
                },
            ]
        }

        picked = pick_next_available_token()

        self.assertIsNotNone(picked)
        assert picked is not None
        self.assertEqual(picked.token_id, "tok-1")

    def test_pick_next_available_token_returns_none_when_no_candidates(self):
        self.mock_table.scan.return_value = {"Items": []}

        picked = pick_next_available_token()

        self.assertIsNone(picked)
        self.mock_table.update_item.assert_not_called()

    def test_get_token_returns_item_including_ingest_secret(self):
        self.mock_table.get_item.return_value = {
            "Item": {
                "TokenId": "tok-1",
                "DisplayName": "team-a",
                "Enabled": True,
                "CreatedAt": 1,
                "IngestSecret": "secret-abc",
            }
        }

        token = get_token("tok-1")

        self.assertIsNotNone(token)
        assert token is not None
        self.assertEqual(token.token_id, "tok-1")
        self.assertEqual(token.ingest_secret, "secret-abc")
        self.mock_table.get_item.assert_called_once_with(Key={"TokenId": "tok-1"})

    def test_get_token_returns_none_when_missing(self):
        self.mock_table.get_item.return_value = {}

        token = get_token("tok-missing")

        self.assertIsNone(token)


if __name__ == "__main__":
    unittest.main()
