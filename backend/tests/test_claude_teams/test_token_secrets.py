import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")
from app.claude_teams.token_secrets import (
    REGISTRATION_SECRET_NAME,
    delete_claude_teams_token,
    get_claude_teams_token,
    get_or_create_registration_secret,
    regenerate_registration_secret,
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

    @patch("boto3.client")
    def test_get_or_create_registration_secret_returns_existing(
        self, mock_boto3_client
    ):
        mock_client = MagicMock()
        mock_boto3_client.return_value = mock_client
        mock_client.get_secret_value.return_value = {"SecretString": "existing-secret"}

        value = get_or_create_registration_secret()

        self.assertEqual(value, "existing-secret")
        mock_client.get_secret_value.assert_called_once_with(
            SecretId=REGISTRATION_SECRET_NAME
        )
        mock_client.create_secret.assert_not_called()

    @patch("boto3.client")
    def test_get_or_create_registration_secret_mints_on_first_call(
        self, mock_boto3_client
    ):
        from botocore.exceptions import ClientError

        mock_client = MagicMock()
        mock_boto3_client.return_value = mock_client
        mock_client.get_secret_value.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "nope"}},
            "GetSecretValue",
        )

        value = get_or_create_registration_secret()

        self.assertTrue(len(value) > 0)
        mock_client.create_secret.assert_called_once()
        kwargs = mock_client.create_secret.call_args.kwargs
        self.assertEqual(kwargs["Name"], REGISTRATION_SECRET_NAME)
        self.assertEqual(kwargs["SecretString"], value)

    @patch("boto3.client")
    def test_get_or_create_registration_secret_handles_create_race(
        self, mock_boto3_client
    ):
        from botocore.exceptions import ClientError

        mock_client = MagicMock()
        mock_boto3_client.return_value = mock_client
        mock_client.get_secret_value.side_effect = [
            ClientError(
                {"Error": {"Code": "ResourceNotFoundException", "Message": "nope"}},
                "GetSecretValue",
            ),
            {"SecretString": "winner-secret"},
        ]
        mock_client.create_secret.side_effect = ClientError(
            {"Error": {"Code": "ResourceExistsException", "Message": "already exists"}},
            "CreateSecret",
        )

        value = get_or_create_registration_secret()

        self.assertEqual(value, "winner-secret")

    @patch("boto3.client")
    def test_regenerate_registration_secret_updates_existing(self, mock_boto3_client):
        mock_client = MagicMock()
        mock_boto3_client.return_value = mock_client
        mock_client.describe_secret.return_value = {"ARN": "arn:aws:secretsmanager:..."}

        new_secret = regenerate_registration_secret()

        mock_client.update_secret.assert_called_once_with(
            SecretId=REGISTRATION_SECRET_NAME, SecretString=new_secret
        )
        mock_client.create_secret.assert_not_called()

    @patch("boto3.client")
    def test_regenerate_registration_secret_creates_when_missing(
        self, mock_boto3_client
    ):
        from botocore.exceptions import ClientError

        mock_client = MagicMock()
        mock_boto3_client.return_value = mock_client
        mock_client.describe_secret.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "nope"}},
            "DescribeSecret",
        )

        new_secret = regenerate_registration_secret()

        mock_client.create_secret.assert_called_once_with(
            Name=REGISTRATION_SECRET_NAME, SecretString=new_secret
        )


if __name__ == "__main__":
    unittest.main()
