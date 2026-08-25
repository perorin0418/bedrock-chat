import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")
from app.usecases.claude_teams_admin import (
    create_claude_teams_token,
    delete_claude_teams_token_usecase,
    list_claude_teams_tokens,
    update_claude_teams_token,
)
from app.claude_teams.token_repository import ClaudeTeamsTokenItem


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


if __name__ == "__main__":
    unittest.main()
