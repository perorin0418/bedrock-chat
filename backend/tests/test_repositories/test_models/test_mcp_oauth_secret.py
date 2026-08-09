import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, ".")
from app.repositories.models.mcp_oauth_secret import (
    get_mcp_oauth_blob,
    is_mcp_oauth_connected,
)


class TestMcpOauthSecret(unittest.TestCase):
    def test_is_connected_false_when_arn_is_none(self):
        self.assertFalse(is_mcp_oauth_connected(None, "atlassian"))

    @patch("app.repositories.models.mcp_oauth_secret.get_api_key_from_secret_manager")
    def test_is_connected_true_when_tokens_present(self, mock_get):
        mock_get.return_value = (
            '{"atlassian": {"client_info": {"client_id": "c1"}, '
            '"tokens": {"access_token": "a1"}}}'
        )
        self.assertTrue(
            is_mcp_oauth_connected("arn:aws:secretsmanager:...", "atlassian")
        )

    @patch("app.repositories.models.mcp_oauth_secret.get_api_key_from_secret_manager")
    def test_is_connected_false_when_label_missing(self, mock_get):
        mock_get.return_value = "{}"
        self.assertFalse(
            is_mcp_oauth_connected("arn:aws:secretsmanager:...", "atlassian")
        )

    @patch("app.repositories.models.mcp_oauth_secret.get_api_key_from_secret_manager")
    def test_is_connected_false_on_error(self, mock_get):
        mock_get.side_effect = Exception("boom")
        self.assertFalse(
            is_mcp_oauth_connected("arn:aws:secretsmanager:...", "atlassian")
        )

    @patch("app.repositories.models.mcp_oauth_secret.get_api_key_from_secret_manager")
    def test_get_mcp_oauth_blob_parses_json(self, mock_get):
        mock_get.return_value = '{"label1": {"tokens": {"access_token": "a"}}}'
        blob = get_mcp_oauth_blob("arn:aws:secretsmanager:...")
        self.assertEqual(blob["label1"]["tokens"]["access_token"], "a")


if __name__ == "__main__":
    unittest.main()
