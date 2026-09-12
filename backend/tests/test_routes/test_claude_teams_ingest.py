import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, ".")
from app.routes.claude_teams_ingest import post_claude_teams_agent_version
from app.routes.schemas.claude_teams import ClaudeTeamsAgentReleaseInput
from app.usecases.claude_teams_admin import (
    AgentReleaseUnavailableError,
    InvalidIngestSecretError,
)
from fastapi import HTTPException

# The route handler is called directly rather than through a TestClient:
# importing the full FastAPI app pulls in Cognito/DynamoDB-dependent
# module-level setup that has nothing to do with the status-code mapping
# these tests exist to pin down.


class TestPostClaudeTeamsAgentVersion(unittest.TestCase):
    @patch("app.routes.claude_teams_ingest.get_agent_release")
    def test_returns_version_digest_and_url(self, mock_get_release):
        mock_get_release.return_value = {
            "version": "2026.09.10-abc1234",
            "sha256": "e" * 64,
            "download_url": "https://signed.example/agent.exe",
        }

        result = post_claude_teams_agent_version(
            token_id="tok-1",
            body=ClaudeTeamsAgentReleaseInput(ingest_secret="ingest-secret-1"),
        )

        mock_get_release.assert_called_once_with(
            token_id="tok-1", ingest_secret="ingest-secret-1"
        )
        self.assertEqual(result.version, "2026.09.10-abc1234")
        self.assertEqual(result.sha256, "e" * 64)
        self.assertEqual(result.download_url, "https://signed.example/agent.exe")

    @patch("app.routes.claude_teams_ingest.get_agent_release")
    def test_bad_secret_is_401_with_a_non_disclosing_detail(self, mock_get_release):
        mock_get_release.side_effect = InvalidIngestSecretError("nope")

        with self.assertRaises(HTTPException) as ctx:
            post_claude_teams_agent_version(
                token_id="tok-1",
                body=ClaudeTeamsAgentReleaseInput(ingest_secret="wrong"),
            )

        self.assertEqual(ctx.exception.status_code, 401)
        # Deliberately does not say which of the two was wrong, so a
        # guessed token_id can't be confirmed.
        self.assertEqual(ctx.exception.detail, "Invalid token_id or ingest_secret.")

    @patch("app.routes.claude_teams_ingest.get_agent_release")
    def test_no_published_release_is_404_not_500(self, mock_get_release):
        # A deployment that still distributes updates by hand is a valid
        # configuration, so the agent must be able to treat this as a
        # routine no-op rather than an error to warn the member about.
        mock_get_release.side_effect = AgentReleaseUnavailableError("nothing published")

        with self.assertRaises(HTTPException) as ctx:
            post_claude_teams_agent_version(
                token_id="tok-1",
                body=ClaudeTeamsAgentReleaseInput(ingest_secret="ingest-secret-1"),
            )

        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
