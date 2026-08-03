import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")
from app.repositories.usage_limit import get_usage_since, record_usage


class TestUsageLimitRepository(unittest.TestCase):
    def setUp(self):
        self.patcher1 = patch("boto3.resource")
        self.mock_boto3_resource = self.patcher1.start()

        self.mock_table = MagicMock()
        self.mock_boto3_resource.return_value.Table.return_value = self.mock_table

        os.environ["USAGE_LEDGER_TABLE_NAME"] = "test-usage-ledger-table"
        os.environ["BEDROCK_REGION"] = "us-east-1"

        # `app.repositories.usage_limit.USAGE_LEDGER_TABLE_NAME` is bound at
        # module-import time (before setUp runs), so setting the env var
        # above has no effect on it. Patch the already-imported module
        # attribute directly so it reflects a configured table by default.
        self.patcher2 = patch(
            "app.repositories.usage_limit.USAGE_LEDGER_TABLE_NAME",
            "test-usage-ledger-table",
        )
        self.patcher2.start()

    def tearDown(self):
        self.patcher1.stop()
        self.patcher2.stop()
        os.environ.pop("USAGE_LEDGER_TABLE_NAME", None)
        os.environ.pop("BEDROCK_REGION", None)

    @patch(
        "app.repositories.usage_limit.get_current_time", return_value=1_700_000_000_000
    )
    def test_record_usage_puts_expected_item(self, mock_get_current_time):
        record_usage(user_id="user-1", price=0.0123)

        self.mock_table.put_item.assert_called_once()
        item = self.mock_table.put_item.call_args.kwargs["Item"]
        self.assertEqual(item["PK"], "user-1")
        self.assertEqual(item["SK"], 1_700_000_000_000)
        self.assertAlmostEqual(float(item["Price"]), 0.0123)
        # TTL = now(seconds) + 8日
        self.assertEqual(item["expire"], 1_700_000_000 + 8 * 24 * 60 * 60)

    def test_get_usage_since_sums_prices_in_window(self):
        self.mock_table.query.return_value = {
            "Items": [
                {"PK": "user-1", "SK": 1000, "Price": "1.5"},
                {"PK": "user-1", "SK": 2000, "Price": "2.25"},
            ]
        }

        total = get_usage_since(user_id="user-1", since_ms=500)

        self.assertAlmostEqual(total, 3.75)
        self.mock_table.query.assert_called_once()

    def test_get_usage_since_returns_zero_when_no_items(self):
        self.mock_table.query.return_value = {"Items": []}

        total = get_usage_since(user_id="user-1", since_ms=500)

        self.assertEqual(total, 0.0)

    def test_get_usage_since_paginates_with_last_evaluated_key(self):
        self.mock_table.query.side_effect = [
            {
                "Items": [{"PK": "user-1", "SK": 1000, "Price": "1.0"}],
                "LastEvaluatedKey": {"PK": "user-1", "SK": 1000},
            },
            {"Items": [{"PK": "user-1", "SK": 2000, "Price": "2.0"}]},
        ]

        total = get_usage_since(user_id="user-1", since_ms=500)

        self.assertAlmostEqual(total, 3.0)
        self.assertEqual(self.mock_table.query.call_count, 2)

    @patch("app.repositories.usage_limit.USAGE_LEDGER_TABLE_NAME", "")
    def test_record_usage_skips_write_when_table_name_is_empty(self):
        record_usage("user-1", 0.01)

        self.mock_table.put_item.assert_not_called()

    def test_record_usage_swallows_put_item_exception(self):
        self.mock_table.put_item.side_effect = Exception("boom")

        record_usage("user-1", 0.01)

        self.mock_table.put_item.assert_called_once()


if __name__ == "__main__":
    unittest.main()
