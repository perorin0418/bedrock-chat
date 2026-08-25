import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, ".")
from app.claude_teams.chat import converse_with_claude_teams
from app.claude_teams.errors import ClaudeTeamsAllTokensUnavailableError
from app.claude_teams.token_repository import ClaudeTeamsTokenItem
from app.repositories.models.conversation import SimpleMessageModel, TextContentModel
from app.routes.schemas.conversation import ChatInput, MessageInput


def _make_chat_input(model="claude-teams-sonnet"):
    return ChatInput(
        conversation_id="conv-1",
        message=MessageInput(
            role="user",
            content=[{"content_type": "text", "body": "hello"}],
            model=model,
            parent_message_id=None,
        ),
    )


def _make_messages():
    return [
        SimpleMessageModel(
            role="user",
            content=[TextContentModel(content_type="text", body="hello")],
        )
    ]


class TestConverseWithClaudeTeams(unittest.TestCase):
    @patch("app.claude_teams.chat.pick_next_available_token")
    def test_raises_when_no_token_available(self, mock_pick):
        mock_pick.return_value = None

        with self.assertRaises(ClaudeTeamsAllTokensUnavailableError):
            converse_with_claude_teams(
                bot=None,
                chat_input=_make_chat_input(),
                instructions=[],
                messages=_make_messages(),
            )

    @patch("app.claude_teams.chat._run_claude_query")
    @patch("app.claude_teams.chat.pick_next_available_token")
    @patch("app.claude_teams.chat.get_claude_teams_token", return_value="sk-oauth-abc")
    def test_returns_on_stop_input_with_zero_price(
        self, mock_get_token, mock_pick, mock_run_query
    ):
        mock_pick.return_value = ClaudeTeamsTokenItem(
            token_id="tok-1", display_name="a", enabled=True, created_at=1
        )
        mock_run_query.return_value = {
            "text": "Hi there!",
            "input_tokens": 10,
            "output_tokens": 5,
        }

        result = converse_with_claude_teams(
            bot=None,
            chat_input=_make_chat_input(),
            instructions=[],
            messages=_make_messages(),
        )

        self.assertEqual(result["price"], 0.0)
        self.assertEqual(result["input_token_count"], 10)
        self.assertEqual(result["output_token_count"], 5)
        self.assertEqual(result["message"].content[0].body, "Hi there!")


if __name__ == "__main__":
    unittest.main()
