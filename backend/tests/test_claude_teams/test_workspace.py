import os
import sys
import unittest

sys.path.insert(0, ".")
from app.claude_teams.workspace import claude_teams_workspace


class TestClaudeTeamsWorkspace(unittest.TestCase):
    def test_writes_claude_md_with_joined_instructions(self):
        with claude_teams_workspace(["Be concise.", "Always answer in Japanese."]) as workspace_dir:
            self.assertTrue(os.path.isdir(workspace_dir))
            claude_md_path = os.path.join(workspace_dir, "CLAUDE.md")
            self.assertTrue(os.path.isfile(claude_md_path))
            with open(claude_md_path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("Be concise.", content)
            self.assertIn("Always answer in Japanese.", content)

    def test_does_not_write_claude_md_when_no_instructions(self):
        with claude_teams_workspace([]) as workspace_dir:
            self.assertTrue(os.path.isdir(workspace_dir))
            claude_md_path = os.path.join(workspace_dir, "CLAUDE.md")
            self.assertFalse(os.path.isfile(claude_md_path))

    def test_deletes_workspace_dir_on_exit(self):
        captured_dir = None
        with claude_teams_workspace(["hello"]) as workspace_dir:
            captured_dir = workspace_dir
            self.assertTrue(os.path.isdir(captured_dir))

        self.assertFalse(os.path.isdir(captured_dir))

    def test_deletes_workspace_dir_on_exception(self):
        captured_dir = None
        with self.assertRaises(ValueError):
            with claude_teams_workspace(["hello"]) as workspace_dir:
                captured_dir = workspace_dir
                raise ValueError("boom")

        self.assertFalse(os.path.isdir(captured_dir))


if __name__ == "__main__":
    unittest.main()
