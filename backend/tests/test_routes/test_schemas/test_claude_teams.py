import sys
import unittest

sys.path.insert(0, ".")
from app.routes.schemas.claude_teams import CreateClaudeTeamsTokenInput


class TestCreateClaudeTeamsTokenInput(unittest.TestCase):
    def test_strips_leading_and_trailing_whitespace(self):
        model = CreateClaudeTeamsTokenInput(
            display_name="team-a", token_value="  sk-ant-oat01-abc123  "
        )
        self.assertEqual(model.token_value, "sk-ant-oat01-abc123")

    def test_strips_embedded_whitespace_from_copy_paste(self):
        # Reproduces the real failure that motivated this validator: a
        # stray space landing in the middle of a pasted token (e.g. from
        # a wrapped terminal line) silently produces a token string that
        # Anthropic rejects with `authentication_error: OAuth access
        # token is invalid.` even though it "looks right" at a glance.
        model = CreateClaudeTeamsTokenInput(
            display_name="team-a",
            token_value="sk-ant-oat01-blFI5WuUoXjJXURbKKxmZMhRLokH9SEJivB0CFj7zhza6zJWR8GTjc  DkxwXDknFcFKzZiDnmQ95gp-r_8Yy0iw-OBvhKAAA",
        )
        self.assertNotIn(" ", model.token_value)
        self.assertEqual(
            model.token_value,
            "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",
        )

    def test_strips_embedded_newlines_and_tabs(self):
        model = CreateClaudeTeamsTokenInput(
            display_name="team-a", token_value="sk-ant-oat01-abc\n123\tdef"
        )
        self.assertEqual(model.token_value, "sk-ant-oat01-abc123def")

    def test_leaves_clean_token_unchanged(self):
        model = CreateClaudeTeamsTokenInput(
            display_name="team-a", token_value="sk-ant-oat01-clean-token-value"
        )
        self.assertEqual(model.token_value, "sk-ant-oat01-clean-token-value")


if __name__ == "__main__":
    unittest.main()
