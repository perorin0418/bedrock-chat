import os
import sys
import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")
from app.claude_teams.usage_history_repository import (
    get_latest_usage_snapshot,
    list_usage_history,
)


class TestClaudeTeamsUsageHistoryRepository(unittest.TestCase):
    def setUp(self):
        self.patcher = patch("boto3.resource")
        self.mock_boto3_resource = self.patcher.start()
        self.mock_table = MagicMock()
        self.mock_boto3_resource.return_value.Table.return_value = self.mock_table

        os.environ["CLAUDE_TEAMS_USAGE_HISTORY_TABLE_NAME"] = (
            "test-claude-teams-usage-history-table"
        )
        os.environ["BEDROCK_REGION"] = "us-east-1"

    def tearDown(self):
        self.patcher.stop()
        os.environ.pop("CLAUDE_TEAMS_USAGE_HISTORY_TABLE_NAME", None)
        os.environ.pop("BEDROCK_REGION", None)

    def test_list_usage_history_returns_items_from_query(self):
        self.mock_table.query.return_value = {
            "Items": [
                {
                    "TokenId": "tok-1",
                    "SampledAtMs": 1_700_000_000_000,
                    "FetchStatus": "ok",
                    "FiveHourUtilization": Decimal("12.5"),
                    "FiveHourResetsAt": "2026-05-14T19:40:00Z",
                    "SevenDayUtilization": Decimal("2.0"),
                    "SevenDayResetsAt": "2026-05-21T16:00:01Z",
                }
            ]
        }

        items = list_usage_history("tok-1", since_ms=0, until_ms=2_000_000_000_000)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].token_id, "tok-1")
        self.assertEqual(items[0].fetch_status, "ok")
        self.assertEqual(items[0].five_hour_utilization, 12.5)
        self.assertEqual(items[0].seven_day_utilization, 2.0)
        self.assertIsNone(items[0].fetch_error_message)

    def test_list_usage_history_paginates(self):
        self.mock_table.query.side_effect = [
            {
                "Items": [
                    {
                        "TokenId": "tok-1",
                        "SampledAtMs": 1,
                        "FetchStatus": "ok",
                    }
                ],
                "LastEvaluatedKey": {"TokenId": "tok-1", "SampledAtMs": 1},
            },
            {
                "Items": [
                    {
                        "TokenId": "tok-1",
                        "SampledAtMs": 2,
                        "FetchStatus": "ok",
                    }
                ],
            },
        ]

        items = list_usage_history("tok-1", since_ms=0, until_ms=10)

        self.assertEqual([i.sampled_at_ms for i in items], [1, 2])
        self.assertEqual(self.mock_table.query.call_count, 2)

    def test_list_usage_history_preserves_auth_error_status_and_message(self):
        self.mock_table.query.return_value = {
            "Items": [
                {
                    "TokenId": "tok-1",
                    "SampledAtMs": 1_700_000_000_000,
                    "FetchStatus": "auth_error",
                    "FetchErrorMessage": "401 Unauthorized (token expired or revoked)",
                }
            ]
        }

        items = list_usage_history("tok-1", since_ms=0, until_ms=2_000_000_000_000)

        self.assertEqual(items[0].fetch_status, "auth_error")
        self.assertEqual(
            items[0].fetch_error_message,
            "401 Unauthorized (token expired or revoked)",
        )
        # Usage-limit fields must remain independently None/unset when the
        # sync couldn't reach the usage endpoint at all — auth failure and
        # usage-limit utilization are unrelated signals.
        self.assertIsNone(items[0].five_hour_utilization)
        self.assertIsNone(items[0].seven_day_utilization)

    def test_get_latest_usage_snapshot_queries_descending_with_limit_one(self):
        self.mock_table.query.return_value = {
            "Items": [
                {
                    "TokenId": "tok-1",
                    "SampledAtMs": 1_700_000_003_600_000,
                    "FetchStatus": "ok",
                    "FiveHourUtilization": Decimal("50"),
                }
            ]
        }

        snapshot = get_latest_usage_snapshot("tok-1")

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.sampled_at_ms, 1_700_000_003_600_000)
        kwargs = self.mock_table.query.call_args.kwargs
        self.assertFalse(kwargs["ScanIndexForward"])
        self.assertEqual(kwargs["Limit"], 1)

    def test_get_latest_usage_snapshot_returns_none_when_no_items(self):
        self.mock_table.query.return_value = {"Items": []}

        snapshot = get_latest_usage_snapshot("tok-1")

        self.assertIsNone(snapshot)


if __name__ == "__main__":
    unittest.main()
