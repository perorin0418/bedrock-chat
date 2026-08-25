import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, ".")


class TestClaudeTeamsModuleWiring(unittest.TestCase):
    def test_all_claude_teams_modules_import_cleanly(self):
        import app.claude_teams.chat  # noqa: F401
        import app.claude_teams.errors  # noqa: F401
        import app.claude_teams.mcp_bridge  # noqa: F401
        import app.claude_teams.models  # noqa: F401
        import app.claude_teams.token_repository  # noqa: F401
        import app.claude_teams.token_secrets  # noqa: F401
        import app.claude_teams.workspace  # noqa: F401
        import app.usecases.claude_teams_admin  # noqa: F401

    def test_type_model_name_includes_all_teams_models(self):
        from typing import get_args
        from app.routes.schemas.conversation import type_model_name

        names = get_args(type_model_name)
        for expected in [
            "claude-teams-opus",
            "claude-teams-sonnet",
            "claude-teams-haiku",
            "claude-teams-fable",
        ]:
            self.assertIn(expected, names)

    def test_is_claude_teams_model_matches_type_model_name_entries(self):
        from typing import get_args
        from app.claude_teams.models import CLAUDE_TEAMS_MODEL_IDS
        from app.routes.schemas.conversation import type_model_name

        names = set(get_args(type_model_name))
        for teams_model in CLAUDE_TEAMS_MODEL_IDS:
            self.assertIn(teams_model, names)

    @patch("app.claude_teams.chat.converse_with_claude_teams")
    def test_usecases_chat_module_imports_claude_teams_chat_lazily(
        self, mock_converse
    ):
        # Importing app.usecases.chat must not fail even though it lazily
        # imports app.claude_teams.chat / app.claude_teams.models inside
        # chat().
        import app.usecases.chat  # noqa: F401


if __name__ == "__main__":
    unittest.main()
