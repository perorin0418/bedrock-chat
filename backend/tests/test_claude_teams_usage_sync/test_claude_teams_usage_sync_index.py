import json
import os
import sys
import unittest
import urllib.error
from decimal import Decimal
from unittest.mock import MagicMock, patch

os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
os.environ["CLAUDE_TEAMS_USAGE_SYNC_ROLE_ARN"] = (
    "arn:aws:iam::123456789012:role/ClaudeTeamsUsageSyncRole"
)
os.environ["CLAUDE_TEAMS_TOKEN_TABLE_NAME"] = "test-claude-teams-token-table"
os.environ["CLAUDE_TEAMS_USAGE_HISTORY_TABLE_NAME"] = (
    "test-claude-teams-usage-history-table"
)

# claude_teams_usage_sync/index.py is a standalone Lambda module (not part
# of the `app` package). It's named "index.py" the same as
# claude_code_cost_sync's Lambda module -- a plain `sys.path.insert` +
# `import index` would collide with that other test module's cached
# sys.modules["index"] whenever both are collected in the same pytest
# process. Load this one under a private, collision-proof module name via
# importlib instead.
import importlib.util

_MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "claude_teams_usage_sync", "index.py"
)
_spec = importlib.util.spec_from_file_location(
    "claude_teams_usage_sync_index", _MODULE_PATH
)
assert _spec is not None and _spec.loader is not None
claude_teams_usage_sync = importlib.util.module_from_spec(_spec)
sys.modules["claude_teams_usage_sync_index"] = claude_teams_usage_sync
_spec.loader.exec_module(claude_teams_usage_sync)


class TestParseUsageBody(unittest.TestCase):
    def test_parses_both_buckets(self):
        body = {
            "five_hour": {"utilization": 12.0, "resets_at": "2026-05-14T19:40:00Z"},
            "seven_day": {"utilization": 2.0, "resets_at": "2026-05-21T16:00:01Z"},
        }
        snapshot = claude_teams_usage_sync._parse_usage_body(body)

        self.assertEqual(snapshot["fetch_status"], "ok")
        self.assertEqual(snapshot["five_hour_utilization"], 12.0)
        self.assertEqual(snapshot["five_hour_resets_at"], "2026-05-14T19:40:00Z")
        self.assertEqual(snapshot["seven_day_utilization"], 2.0)
        self.assertEqual(snapshot["seven_day_resets_at"], "2026-05-21T16:00:01Z")

    def test_missing_buckets_default_to_none(self):
        snapshot = claude_teams_usage_sync._parse_usage_body({})

        self.assertEqual(snapshot["fetch_status"], "ok")
        self.assertIsNone(snapshot["five_hour_utilization"])
        self.assertIsNone(snapshot["seven_day_utilization"])


class TestFetchUsageSnapshot(unittest.TestCase):
    def setUp(self):
        self.patcher = patch("claude_teams_usage_sync_index.secretsmanager")
        self.mock_secretsmanager = self.patcher.start()
        self.mock_secretsmanager.get_secret_value.return_value = {
            "SecretString": "sk-ant-oat-test"
        }

    def tearDown(self):
        self.patcher.stop()

    @patch("claude_teams_usage_sync_index.urllib.request.urlopen")
    def test_returns_ok_snapshot_on_200(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {
                "five_hour": {"utilization": 5.0, "resets_at": "2026-01-01T00:00:00Z"},
                "seven_day": {"utilization": 1.0, "resets_at": "2026-01-07T00:00:00Z"},
            }
        ).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        snapshot = claude_teams_usage_sync._fetch_usage_snapshot("tok-1")

        self.assertEqual(snapshot["fetch_status"], "ok")
        self.assertEqual(snapshot["five_hour_utilization"], 5.0)
        # Usage-limit consumption and token validity are independent: a
        # successful "ok" fetch says nothing was wrong with the token.
        request = mock_urlopen.call_args[0][0]
        self.assertEqual(
            request.get_header("Authorization"), "Bearer sk-ant-oat-test"
        )
        self.assertEqual(request.get_header("Anthropic-beta"), "oauth-2025-04-20")

    @patch("claude_teams_usage_sync_index.urllib.request.urlopen")
    def test_401_maps_to_auth_error_not_usage_limit(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://api.anthropic.com/api/oauth/usage",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=None,
        )

        snapshot = claude_teams_usage_sync._fetch_usage_snapshot("tok-1")

        # 401 means the token itself is invalid ("期限切れ") -- this must
        # never be conflated with usage-limit utilization, which this
        # snapshot deliberately carries no value for.
        self.assertEqual(snapshot["fetch_status"], "auth_error")
        self.assertNotIn("five_hour_utilization", snapshot)
        self.assertNotIn("seven_day_utilization", snapshot)

    @patch("claude_teams_usage_sync_index.urllib.request.urlopen")
    def test_non_401_http_error_maps_to_generic_error(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://api.anthropic.com/api/oauth/usage",
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=None,
        )

        snapshot = claude_teams_usage_sync._fetch_usage_snapshot("tok-1")

        # A transient 5xx is not evidence the token is invalid, so it must
        # be classified distinctly from auth_error.
        self.assertEqual(snapshot["fetch_status"], "error")


class TestWriteUsageHistoryItem(unittest.TestCase):
    def setUp(self):
        self.mock_table = MagicMock()

    def test_writes_ok_snapshot_with_both_buckets(self):
        claude_teams_usage_sync._write_usage_history_item(
            self.mock_table,
            "tok-1",
            1_700_000_000_000,
            {
                "fetch_status": "ok",
                "five_hour_utilization": 12.5,
                "five_hour_resets_at": "2026-01-01T00:00:00Z",
                "seven_day_utilization": 3.0,
                "seven_day_resets_at": "2026-01-07T00:00:00Z",
            },
        )

        self.mock_table.put_item.assert_called_once()
        item = self.mock_table.put_item.call_args.kwargs["Item"]
        self.assertEqual(item["TokenId"], "tok-1")
        self.assertEqual(item["SampledAtMs"], 1_700_000_000_000)
        self.assertEqual(item["FetchStatus"], "ok")
        self.assertEqual(item["FiveHourUtilization"], Decimal("12.5"))
        self.assertEqual(item["SevenDayUtilization"], Decimal("3.0"))
        self.assertNotIn("FetchErrorMessage", item)
        self.assertIn("expire", item)

    def test_writes_auth_error_snapshot_without_usage_fields(self):
        claude_teams_usage_sync._write_usage_history_item(
            self.mock_table,
            "tok-1",
            1_700_000_000_000,
            {
                "fetch_status": "auth_error",
                "fetch_error_message": "401 Unauthorized (token expired or revoked)",
            },
        )

        item = self.mock_table.put_item.call_args.kwargs["Item"]
        self.assertEqual(item["FetchStatus"], "auth_error")
        self.assertEqual(
            item["FetchErrorMessage"],
            "401 Unauthorized (token expired or revoked)",
        )
        # An auth_error snapshot must not accidentally carry stale/zero
        # usage-limit values -- the two signals are unrelated.
        self.assertNotIn("FiveHourUtilization", item)
        self.assertNotIn("SevenDayUtilization", item)


class TestHandler(unittest.TestCase):
    def setUp(self):
        self.patcher_sts = patch("claude_teams_usage_sync_index.sts")
        self.mock_sts = self.patcher_sts.start()
        self.mock_sts.assume_role.return_value = {
            "Credentials": {
                "AccessKeyId": "AKIA",
                "SecretAccessKey": "secret",
                "SessionToken": "token",
            }
        }

        self.patcher_session = patch("claude_teams_usage_sync_index.boto3.Session")
        self.mock_session_cls = self.patcher_session.start()
        self.mock_session = MagicMock()
        self.mock_session_cls.return_value = self.mock_session

        self.mock_token_table = MagicMock()
        self.mock_usage_history_table = MagicMock()
        self.mock_session.resource.return_value.Table.side_effect = [
            self.mock_token_table,
            self.mock_usage_history_table,
        ]

    def tearDown(self):
        self.patcher_sts.stop()
        self.patcher_session.stop()

    @patch("claude_teams_usage_sync_index._fetch_usage_snapshot")
    @patch("claude_teams_usage_sync_index._now_ms", return_value=1_700_000_000_000)
    def test_handler_samples_every_token_and_isolates_per_token_failures(
        self, mock_now_ms, mock_fetch
    ):
        self.mock_token_table.scan.return_value = {
            "Items": [
                {"TokenId": "tok-ok"},
                {"TokenId": "tok-failing"},
            ]
        }
        mock_fetch.side_effect = [
            {"fetch_status": "ok", "five_hour_utilization": 1.0},
            RuntimeError("network blip"),
        ]

        claude_teams_usage_sync.handler({}, None)

        self.assertEqual(self.mock_usage_history_table.put_item.call_count, 2)
        items = [
            call.kwargs["Item"]
            for call in self.mock_usage_history_table.put_item.call_args_list
        ]
        self.assertEqual(items[0]["TokenId"], "tok-ok")
        self.assertEqual(items[0]["FetchStatus"], "ok")
        # The failing token's exception must not stop the other token from
        # being written, and must fall back to an "error" snapshot.
        self.assertEqual(items[1]["TokenId"], "tok-failing")
        self.assertEqual(items[1]["FetchStatus"], "error")

    def test_handler_skips_tokens_without_token_id(self):
        self.mock_token_table.scan.return_value = {"Items": [{}]}

        claude_teams_usage_sync.handler({}, None)

        self.mock_usage_history_table.put_item.assert_not_called()


if __name__ == "__main__":
    unittest.main()
