import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")
from app.claude_teams.token_secrets import (
    delete_claude_teams_token,
    get_claude_teams_token,
    store_claude_teams_token,
)


class TestClaudeTeamsTokenSecrets(unittest.TestCase):
    @patch("boto3.client")
    def test_store_creates_new_secret(self, mock_boto3_client):
        mock_client = MagicMock()
        mock_boto3_client.return_value = mock_client
        mock_client.describe_secret.side_effect = Exception("not used in create path")
        from botocore.exceptions import ClientError

        mock_client.describe_secret.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "nope"}},
            "DescribeSecret",
        )

        store_claude_teams_token("tok-1", "sk-oauth-abc123")

        mock_client.create_secret.assert_called_once()
        kwargs = mock_client.create_secret.call_args.kwargs
        self.assertEqual(kwargs["Name"], "claude-teams-token/tok-1")
        self.assertEqual(kwargs["SecretString"], "sk-oauth-abc123")

    @patch("boto3.client")
    def test_store_updates_existing_secret(self, mock_boto3_client):
        mock_client = MagicMock()
        mock_boto3_client.return_value = mock_client
        mock_client.describe_secret.return_value = {"ARN": "arn:aws:secretsmanager:..."}

        store_claude_teams_token("tok-1", "sk-oauth-new")

        mock_client.update_secret.assert_called_once_with(
            SecretId="claude-teams-token/tok-1", SecretString="sk-oauth-new"
        )
        mock_client.create_secret.assert_not_called()

    @patch("boto3.client")
    def test_get_returns_secret_string(self, mock_boto3_client):
        mock_client = MagicMock()
        mock_boto3_client.return_value = mock_client
        mock_client.get_secret_value.return_value = {"SecretString": "sk-oauth-abc123"}

        value = get_claude_teams_token("tok-1")

        self.assertEqual(value, "sk-oauth-abc123")
        mock_client.get_secret_value.assert_called_once_with(
            SecretId="claude-teams-token/tok-1"
        )

    @patch("boto3.client")
    def test_delete_deletes_secret(self, mock_boto3_client):
        mock_client = MagicMock()
        mock_boto3_client.return_value = mock_client

        delete_claude_teams_token("tok-1")

        mock_client.delete_secret.assert_called_once_with(
            SecretId="claude-teams-token/tok-1", ForceDeleteWithoutRecovery=True
        )


if __name__ == "__main__":
    unittest.main()
