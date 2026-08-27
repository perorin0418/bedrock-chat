import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")
from app.usecases.claude_teams_admin import (
    build_claude_teams_usage_history_csv,
    create_claude_teams_token,
    delete_claude_teams_token_usecase,
    get_claude_teams_token_latest_usage,
    list_claude_teams_tokens,
    update_claude_teams_token,
)
from app.claude_teams.token_repository import ClaudeTeamsTokenItem
from app.claude_teams.usage_history_repository import ClaudeTeamsUsageHistoryItem


class TestClaudeTeamsAdminUsecase(unittest.TestCase):
    @patch("app.usecases.claude_teams_admin.store_claude_teams_token")
    @patch("app.usecases.claude_teams_admin.create_token")
    def test_create_claude_teams_token_stores_secret_then_metadata(
        self, mock_create_token, mock_store_secret
    ):
        mock_create_token.return_value = ClaudeTeamsTokenItem(
            token_id="tok-1", display_name="team-a", enabled=True, created_at=1
        )

        result = create_claude_teams_token(display_name="team-a", token_value="sk-oauth-abc")

        mock_create_token.assert_called_once_with(display_name="team-a")
        mock_store_secret.assert_called_once_with("tok-1", "sk-oauth-abc")
        self.assertEqual(result.token_id, "tok-1")

    @patch("app.usecases.claude_teams_admin.list_tokens")
    def test_list_claude_teams_tokens_returns_repository_result(self, mock_list):
        mock_list.return_value = [
            ClaudeTeamsTokenItem(
                token_id="tok-1", display_name="a", enabled=True, created_at=1
            )
        ]

        result = list_claude_teams_tokens()

        self.assertEqual(result, mock_list.return_value)

    @patch("app.usecases.claude_teams_admin.set_enabled")
    def test_update_claude_teams_token_sets_enabled(self, mock_set_enabled):
        update_claude_teams_token(token_id="tok-1", enabled=False, display_name=None)

        mock_set_enabled.assert_called_once_with("tok-1", False)

    @patch("app.usecases.claude_teams_admin.delete_claude_teams_token")
    @patch("app.usecases.claude_teams_admin.delete_token")
    def test_delete_claude_teams_token_usecase_deletes_both(
        self, mock_delete_token, mock_delete_secret
    ):
        delete_claude_teams_token_usecase("tok-1")

        mock_delete_token.assert_called_once_with("tok-1")
        mock_delete_secret.assert_called_once_with("tok-1")

    @patch("app.usecases.claude_teams_admin.get_latest_usage_snapshot")
    def test_get_claude_teams_token_latest_usage_delegates_to_repository(
        self, mock_get_latest
    ):
        mock_get_latest.return_value = ClaudeTeamsUsageHistoryItem(
            token_id="tok-1", sampled_at_ms=1, fetch_status="ok"
        )

        result = get_claude_teams_token_latest_usage("tok-1")

        mock_get_latest.assert_called_once_with("tok-1")
        self.assertEqual(result, mock_get_latest.return_value)

    @patch("app.usecases.claude_teams_admin.list_usage_history")
    @patch("app.usecases.claude_teams_admin.list_tokens")
    def test_build_csv_for_single_token_includes_header_and_rows(
        self, mock_list_tokens, mock_list_history
    ):
        mock_list_tokens.return_value = [
            ClaudeTeamsTokenItem(
                token_id="tok-1", display_name="team-a", enabled=True, created_at=1
            )
        ]
        mock_list_history.return_value = [
            ClaudeTeamsUsageHistoryItem(
                token_id="tok-1",
                sampled_at_ms=1_700_000_000_000,
                fetch_status="ok",
                five_hour_utilization=12.5,
                five_hour_resets_at="2026-01-01T00:00:00Z",
                seven_day_utilization=3.0,
                seven_day_resets_at="2026-01-07T00:00:00Z",
            )
        ]

        csv_text = build_claude_teams_usage_history_csv(
            since_ms=0, until_ms=2_000_000_000_000, token_id="tok-1"
        )

        lines = csv_text.strip().splitlines()
        self.assertEqual(
            lines[0],
            "token_id,display_name,sampled_at_ms,fetch_status,fetch_error_message,"
            "five_hour_utilization,five_hour_resets_at,seven_day_utilization,"
            "seven_day_resets_at",
        )
        self.assertIn("tok-1", lines[1])
        self.assertIn("ok", lines[1])
        self.assertIn("12.5", lines[1])
        mock_list_history.assert_called_once_with("tok-1", 0, 2_000_000_000_000)

    @patch("app.usecases.claude_teams_admin.list_usage_history")
    @patch("app.usecases.claude_teams_admin.list_tokens")
    def test_build_csv_without_token_id_covers_every_registered_token(
        self, mock_list_tokens, mock_list_history
    ):
        mock_list_tokens.return_value = [
            ClaudeTeamsTokenItem(
                token_id="tok-1", display_name="team-a", enabled=True, created_at=1
            ),
            ClaudeTeamsTokenItem(
                token_id="tok-2", display_name="team-b", enabled=True, created_at=1
            ),
        ]
        mock_list_history.side_effect = [
            [
                ClaudeTeamsUsageHistoryItem(
                    token_id="tok-1", sampled_at_ms=1, fetch_status="ok"
                )
            ],
            [
                ClaudeTeamsUsageHistoryItem(
                    token_id="tok-2",
                    sampled_at_ms=2,
                    fetch_status="auth_error",
                    fetch_error_message="401 Unauthorized (token expired or revoked)",
                )
            ],
        ]

        csv_text = build_claude_teams_usage_history_csv(since_ms=0, until_ms=10)

        lines = csv_text.strip().splitlines()
        self.assertEqual(len(lines), 3)  # header + 2 rows
        self.assertIn("team-a", lines[1])
        self.assertIn("team-b", lines[2])
        # Token-validity failures must show up verbatim in the export,
        # distinct from any usage-limit column.
        self.assertIn("auth_error", lines[2])
        self.assertIn(
            "401 Unauthorized (token expired or revoked)", lines[2]
        )


if __name__ == "__main__":
    unittest.main()
