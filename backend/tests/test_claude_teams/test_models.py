import sys
import unittest

sys.path.insert(0, ".")
from app.claude_teams.models import (
    CLAUDE_TEAMS_MODEL_IDS,
    get_claude_teams_native_model_id,
    is_claude_teams_model,
)


class TestClaudeTeamsModels(unittest.TestCase):
    def test_mapping_contains_all_four_models(self):
        self.assertEqual(
            CLAUDE_TEAMS_MODEL_IDS,
            {
                "claude-teams-opus": "claude-opus-5",
                "claude-teams-sonnet": "claude-sonnet-5",
                "claude-teams-haiku": "claude-haiku-4-5-20251001",
                "claude-teams-fable": "claude-fable-5",
            },
        )

    def test_is_claude_teams_model_true_for_teams_models(self):
        self.assertTrue(is_claude_teams_model("claude-teams-opus"))
        self.assertTrue(is_claude_teams_model("claude-teams-fable"))

    def test_is_claude_teams_model_false_for_other_models(self):
        self.assertFalse(is_claude_teams_model("claude-v5-opus"))
        self.assertFalse(is_claude_teams_model("amazon-nova-lite"))

    def test_get_claude_teams_native_model_id_returns_mapped_value(self):
        self.assertEqual(
            get_claude_teams_native_model_id("claude-teams-sonnet"), "claude-sonnet-5"
        )

    def test_get_claude_teams_native_model_id_raises_for_unknown_model(self):
        with self.assertRaises(ValueError):
            get_claude_teams_native_model_id("claude-v5-opus")


if __name__ == "__main__":
    unittest.main()
