import os
import sys
import unittest
from decimal import Decimal
from unittest.mock import MagicMock, call, patch

os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
os.environ["CLAUDE_CODE_COST_SYNC_ROLE_ARN"] = (
    "arn:aws:iam::123456789012:role/ClaudeCodeCostSyncRole"
)
os.environ["USAGE_LEDGER_TABLE_NAME"] = "test-usage-ledger-table"
os.environ["CLAUDE_CODE_IAM_USER_TABLE_NAME"] = "test-claude-code-iam-user-table"
os.environ["GLUE_DATABASE_NAME"] = "test_claude_code_cur"
os.environ["ATHENA_WORKGROUP"] = "test_wg"
os.environ["RATE_LIMIT_FIVE_HOUR_PARAM_NAME"] = "/test/rate-limit/five-hour-usd-limit"
os.environ["RATE_LIMIT_SEVEN_DAY_PARAM_NAME"] = "/test/rate-limit/seven-day-usd-limit"

# claude_code_cost_sync/index.py is a standalone Lambda module (not part of
# the `app` package), so it needs its own sys.path entry to be importable.
sys.path.insert(0, "claude_code_cost_sync")
import index as claude_code_cost_sync  # noqa: E402


class TestUserIdFromPrincipalArn(unittest.TestCase):
    def test_extracts_user_id(self):
        arn = "arn:aws:iam::123456789012:user/claude-code-abc-123"
        self.assertEqual(
            claude_code_cost_sync._user_id_from_principal_arn(arn), "abc-123"
        )

    def test_returns_none_for_non_claude_code_principal(self):
        arn = "arn:aws:iam::123456789012:role/some-other-role"
        self.assertIsNone(claude_code_cost_sync._user_id_from_principal_arn(arn))


class TestParseAthenaTimestamp(unittest.TestCase):
    def test_parses_with_microseconds(self):
        ms = claude_code_cost_sync._parse_athena_timestamp_to_epoch_ms(
            "2026-08-01 00:00:00.000"
        )
        self.assertEqual(ms, 1785542400000)

    def test_parses_without_microseconds(self):
        ms = claude_code_cost_sync._parse_athena_timestamp_to_epoch_ms(
            "2026-08-01 00:00:00"
        )
        self.assertEqual(ms, 1785542400000)

    def test_raises_on_unrecognized_format(self):
        with self.assertRaises(ValueError):
            claude_code_cost_sync._parse_athena_timestamp_to_epoch_ms("garbage")


class TestBuildCostQuery(unittest.TestCase):
    def test_query_contains_expected_fragments(self):
        query = claude_code_cost_sync._build_cost_query(
            "mydb", "mytable", 8 * 24 * 60 * 60 * 1000
        )
        self.assertIn('FROM "mydb"."mytable"', query)
        self.assertIn("line_item_line_item_type = 'Usage'", query)
        self.assertIn("user/claude-code-%", query)
        self.assertIn("GROUP BY line_item_iam_principal", query)


class TestFindCurTableName(unittest.TestCase):
    def setUp(self):
        self.patcher = patch("index.glue")
        self.mock_glue = self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_returns_first_table_name(self):
        self.mock_glue.get_tables.return_value = {
            "TableList": [{"Name": "claude_code_cur_data"}]
        }
        self.assertEqual(
            claude_code_cost_sync._find_cur_table_name("mydb"), "claude_code_cur_data"
        )

    def test_raises_when_no_tables_yet(self):
        self.mock_glue.get_tables.return_value = {"TableList": []}
        with self.assertRaises(RuntimeError):
            claude_code_cost_sync._find_cur_table_name("mydb")


class TestRunAthenaQuery(unittest.TestCase):
    def setUp(self):
        self.patcher = patch("index.athena")
        self.mock_athena = self.patcher.start()
        self.patcher_sleep = patch("index.time.sleep")
        self.patcher_sleep.start()

    def tearDown(self):
        self.patcher.stop()
        self.patcher_sleep.stop()

    def test_polls_until_succeeded_and_returns_rows(self):
        self.mock_athena.start_query_execution.return_value = {
            "QueryExecutionId": "q-1"
        }
        self.mock_athena.get_query_execution.side_effect = [
            {"QueryExecution": {"Status": {"State": "RUNNING"}}},
            {"QueryExecution": {"Status": {"State": "SUCCEEDED"}}},
        ]
        self.mock_athena.get_query_results.return_value = {
            "ResultSet": {
                "Rows": [
                    {"Data": [{"VarCharValue": "principal_arn"}]},
                    {"Data": [{"VarCharValue": "arn:aws:iam::123:user/claude-code-x"}]},
                ]
            }
        }

        rows = claude_code_cost_sync._run_athena_query("SELECT 1", "wg")

        self.assertEqual(
            rows, [{"principal_arn": "arn:aws:iam::123:user/claude-code-x"}]
        )

    def test_raises_when_failed(self):
        self.mock_athena.start_query_execution.return_value = {
            "QueryExecutionId": "q-1"
        }
        self.mock_athena.get_query_execution.return_value = {
            "QueryExecution": {"Status": {"State": "FAILED"}}
        }

        with self.assertRaises(RuntimeError):
            claude_code_cost_sync._run_athena_query("SELECT 1", "wg")


class TestPaginateQueryResults(unittest.TestCase):
    def setUp(self):
        self.patcher = patch("index.athena")
        self.mock_athena = self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_handles_multiple_pages(self):
        self.mock_athena.get_query_results.side_effect = [
            {
                "ResultSet": {
                    "Rows": [
                        {
                            "Data": [
                                {"VarCharValue": "col_a"},
                                {"VarCharValue": "col_b"},
                            ]
                        },
                        {"Data": [{"VarCharValue": "1"}, {"VarCharValue": "2"}]},
                    ]
                },
                "NextToken": "token-1",
            },
            {
                "ResultSet": {
                    "Rows": [
                        {"Data": [{"VarCharValue": "3"}, {"VarCharValue": "4"}]},
                    ]
                }
            },
        ]

        rows = claude_code_cost_sync._paginate_query_results("q-1")

        self.assertEqual(
            rows,
            [{"col_a": "1", "col_b": "2"}, {"col_a": "3", "col_b": "4"}],
        )


class TestRowsToLedgerItems(unittest.TestCase):
    def test_converts_valid_rows_and_skips_invalid(self):
        rows = [
            {
                "principal_arn": "arn:aws:iam::123:user/claude-code-user-1",
                "usage_day": "2026-08-01 00:00:00.000",
                "total_cost": "1.5",
            },
            {"principal_arn": None, "usage_day": "2026-08-01", "total_cost": "1"},
            {
                "principal_arn": "arn:aws:iam::123:role/not-claude-code",
                "usage_day": "2026-08-01 00:00:00.000",
                "total_cost": "1",
            },
        ]

        items = claude_code_cost_sync._rows_to_ledger_items(rows)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["user_id"], "user-1")
        self.assertEqual(items[0]["total_cost"], Decimal("1.5"))


class TestWriteLedgerItems(unittest.TestCase):
    def test_puts_expected_items(self):
        mock_table = MagicMock()
        items = [
            {
                "user_id": "user-1",
                "day_start_ms": 1785542400000,
                "total_cost": Decimal("2"),
            }
        ]

        claude_code_cost_sync._write_ledger_items(mock_table, items)

        mock_table.put_item.assert_called_once()
        item = mock_table.put_item.call_args.kwargs["Item"]
        self.assertEqual(item["PK"], "user-1")
        self.assertEqual(item["SK"], 1785542400000)
        self.assertEqual(item["Price"], Decimal("2"))
        self.assertEqual(item["Source"], "claude_code_cur_sync")


class TestGetUsageSince(unittest.TestCase):
    def test_sums_price_across_pages(self):
        mock_table = MagicMock()
        mock_table.query.side_effect = [
            {
                "Items": [{"Price": Decimal("1.5")}],
                "LastEvaluatedKey": {"PK": "user-1", "SK": 1},
            },
            {"Items": [{"Price": Decimal("2.5")}]},
        ]

        total = claude_code_cost_sync.get_usage_since(mock_table, "user-1", 0)

        self.assertEqual(total, 4.0)


class TestGetLimit(unittest.TestCase):
    def setUp(self):
        claude_code_cost_sync._limit_cache.clear()
        self.patcher = patch("index.ssm")
        self.mock_ssm = self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        claude_code_cost_sync._limit_cache.clear()

    def test_fetches_and_caches(self):
        self.mock_ssm.get_parameter.return_value = {"Parameter": {"Value": "10"}}

        value = claude_code_cost_sync._get_limit("/param")

        self.assertEqual(value, 10.0)
        self.mock_ssm.get_parameter.assert_called_once()

    def test_falls_back_to_stale_cache_on_error(self):
        with patch("index.time.time", return_value=1000.0):
            claude_code_cost_sync._limit_cache["/param"] = (5.0, 1000.0)
        self.mock_ssm.get_parameter.side_effect = Exception("ssm unavailable")

        with patch("index.time.time", return_value=2000.0):
            value = claude_code_cost_sync._get_limit("/param")

        self.assertEqual(value, 5.0)


class TestDenyPolicyHelpers(unittest.TestCase):
    def test_is_denied_true_when_policy_exists(self):
        mock_iam = MagicMock()
        mock_iam.get_user_policy.return_value = {}
        self.assertTrue(
            claude_code_cost_sync._is_denied(mock_iam, "claude-code-user-1")
        )

    def test_is_denied_false_when_no_such_entity(self):
        mock_iam = MagicMock()

        class NoSuchEntityException(Exception):
            pass

        mock_iam.exceptions.NoSuchEntityException = NoSuchEntityException
        mock_iam.get_user_policy.side_effect = NoSuchEntityException()

        self.assertFalse(
            claude_code_cost_sync._is_denied(mock_iam, "claude-code-user-1")
        )

    def test_apply_deny_puts_policy(self):
        mock_iam = MagicMock()
        claude_code_cost_sync._apply_deny(mock_iam, "claude-code-user-1")
        mock_iam.put_user_policy.assert_called_once()
        self.assertEqual(
            mock_iam.put_user_policy.call_args.kwargs["UserName"],
            "claude-code-user-1",
        )

    def test_remove_deny_swallows_no_such_entity(self):
        mock_iam = MagicMock()

        class NoSuchEntityException(Exception):
            pass

        mock_iam.exceptions.NoSuchEntityException = NoSuchEntityException
        mock_iam.delete_user_policy.side_effect = NoSuchEntityException()

        claude_code_cost_sync._remove_deny(mock_iam, "claude-code-user-1")  # no raise


class TestEvaluateAndApplyDeny(unittest.TestCase):
    def setUp(self):
        claude_code_cost_sync._limit_cache.clear()
        self.patcher_ssm = patch("index.ssm")
        self.mock_ssm = self.patcher_ssm.start()
        self.mock_ssm.get_parameter.return_value = {"Parameter": {"Value": "10"}}
        self.patcher_notify = patch("index._notify")
        self.mock_notify = self.patcher_notify.start()

    def tearDown(self):
        self.patcher_ssm.stop()
        self.patcher_notify.stop()
        claude_code_cost_sync._limit_cache.clear()

    def test_applies_deny_when_seven_day_sum_exceeds_limit(self):
        mock_iam_user_table = MagicMock()
        mock_iam_user_table.scan.return_value = {
            "Items": [{"UserId": "user-1", "IamUserName": "claude-code-user-1"}]
        }
        mock_usage_ledger_table = MagicMock()
        mock_usage_ledger_table.query.return_value = {
            "Items": [{"Price": Decimal("999")}]
        }
        mock_iam_client = MagicMock()

        claude_code_cost_sync._evaluate_and_apply_deny(
            mock_iam_user_table, mock_usage_ledger_table, mock_iam_client
        )

        mock_iam_client.put_user_policy.assert_called_once()
        mock_iam_user_table.update_item.assert_called_once()
        self.assertTrue(
            mock_iam_user_table.update_item.call_args.kwargs[
                "ExpressionAttributeValues"
            ][":deny_active"]
        )
        self.mock_notify.assert_called_once()

    def test_does_not_notify_when_already_denied(self):
        mock_iam_user_table = MagicMock()
        mock_iam_user_table.scan.return_value = {
            "Items": [
                {
                    "UserId": "user-1",
                    "IamUserName": "claude-code-user-1",
                    "DenyActive": True,
                }
            ]
        }
        mock_usage_ledger_table = MagicMock()
        mock_usage_ledger_table.query.return_value = {
            "Items": [{"Price": Decimal("999")}]
        }
        mock_iam_client = MagicMock()

        claude_code_cost_sync._evaluate_and_apply_deny(
            mock_iam_user_table, mock_usage_ledger_table, mock_iam_client
        )

        mock_iam_client.put_user_policy.assert_called_once()
        self.mock_notify.assert_not_called()

    def test_removes_deny_when_under_limit(self):
        mock_iam_user_table = MagicMock()
        mock_iam_user_table.scan.return_value = {
            "Items": [{"UserId": "user-1", "IamUserName": "claude-code-user-1"}]
        }
        mock_usage_ledger_table = MagicMock()
        mock_usage_ledger_table.query.return_value = {"Items": []}
        mock_iam_client = MagicMock()

        claude_code_cost_sync._evaluate_and_apply_deny(
            mock_iam_user_table, mock_usage_ledger_table, mock_iam_client
        )

        mock_iam_client.delete_user_policy.assert_called_once()
        self.assertFalse(
            mock_iam_user_table.update_item.call_args.kwargs[
                "ExpressionAttributeValues"
            ][":deny_active"]
        )

    def test_one_user_failure_does_not_block_others(self):
        mock_iam_user_table = MagicMock()
        mock_iam_user_table.scan.return_value = {
            "Items": [
                {"UserId": "user-1", "IamUserName": "claude-code-user-1"},
                {"UserId": "user-2", "IamUserName": "claude-code-user-2"},
            ]
        }
        mock_usage_ledger_table = MagicMock()
        mock_usage_ledger_table.query.side_effect = [
            Exception("ddb unavailable"),  # user-1: five_hour query fails
            {"Items": []},  # user-2: five_hour
            {"Items": []},  # user-2: seven_day
        ]
        mock_iam_client = MagicMock()

        # Must not raise even though user-1 fails.
        claude_code_cost_sync._evaluate_and_apply_deny(
            mock_iam_user_table, mock_usage_ledger_table, mock_iam_client
        )

        mock_iam_client.delete_user_policy.assert_called_once_with(
            UserName="claude-code-user-2",
            PolicyName=claude_code_cost_sync.DENY_POLICY_NAME,
        )


class TestNotify(unittest.TestCase):
    def setUp(self):
        self.patcher = patch("index.sns")
        self.mock_sns = self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_noop_when_topic_arn_not_configured(self):
        with patch("index.CLAUDE_CODE_NOTIFICATION_TOPIC_ARN", ""):
            claude_code_cost_sync._notify("subject", "message")
        self.mock_sns.publish.assert_not_called()

    def test_publishes_when_topic_arn_configured(self):
        with patch(
            "index.CLAUDE_CODE_NOTIFICATION_TOPIC_ARN",
            "arn:aws:sns:us-east-1:123456789012:topic",
        ):
            claude_code_cost_sync._notify("subject", "message")
        self.mock_sns.publish.assert_called_once_with(
            TopicArn="arn:aws:sns:us-east-1:123456789012:topic",
            Subject="subject",
            Message="message",
        )

    def test_does_not_raise_when_publish_fails(self):
        self.mock_sns.publish.side_effect = Exception("sns unavailable")
        with patch(
            "index.CLAUDE_CODE_NOTIFICATION_TOPIC_ARN",
            "arn:aws:sns:us-east-1:123456789012:topic",
        ):
            claude_code_cost_sync._notify("subject", "message")  # must not raise


class TestHandler(unittest.TestCase):
    def setUp(self):
        self.patcher_sts = patch("index.sts")
        self.mock_sts = self.patcher_sts.start()
        self.mock_sts.assume_role.return_value = {
            "Credentials": {
                "AccessKeyId": "AKIA",
                "SecretAccessKey": "secret",
                "SessionToken": "token",
            }
        }
        self.patcher_session = patch("index.boto3.Session")
        self.mock_session_cls = self.patcher_session.start()
        self.mock_session = MagicMock()
        self.mock_session_cls.return_value = self.mock_session

    def tearDown(self):
        self.patcher_sts.stop()
        self.patcher_session.stop()

    @patch("index._evaluate_and_apply_deny")
    @patch("index._write_ledger_items")
    @patch("index._rows_to_ledger_items")
    @patch("index._run_athena_query")
    @patch("index._build_cost_query")
    @patch("index._find_cur_table_name")
    def test_happy_path_writes_ledger_and_evaluates_deny(
        self,
        mock_find_table,
        mock_build_query,
        mock_run_query,
        mock_rows_to_items,
        mock_write_items,
        mock_evaluate,
    ):
        mock_find_table.return_value = "claude_code_cur_data"
        mock_build_query.return_value = "SELECT 1"
        mock_run_query.return_value = [{"principal_arn": "..."}]
        mock_rows_to_items.return_value = [{"user_id": "user-1"}]

        claude_code_cost_sync.handler({}, MagicMock())

        mock_write_items.assert_called_once()
        mock_evaluate.assert_called_once()

    @patch("index._notify")
    @patch("index._evaluate_and_apply_deny")
    @patch("index._find_cur_table_name")
    def test_query_failure_propagates_notifies_and_skips_deny_evaluation(
        self, mock_find_table, mock_evaluate, mock_notify
    ):
        mock_find_table.side_effect = RuntimeError("no CUR data yet")

        with self.assertRaises(RuntimeError):
            claude_code_cost_sync.handler({}, MagicMock())

        mock_evaluate.assert_not_called()
        mock_notify.assert_called_once()


if __name__ == "__main__":
    unittest.main()
