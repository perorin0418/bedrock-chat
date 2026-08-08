import sys

sys.path.append(".")
import unittest

from pydantic import ValidationError

from app.routes.schemas.bot import McpAuthType, McpConfig


class TestMcpConfigAuthType(unittest.TestCase):
    def test_defaults_to_cognito_client_credentials(self):
        config = McpConfig(
            label="foo",
            endpoint_url="https://example.com/mcp",
            client_id="id-1",
            client_secret="secret-1",
        )
        self.assertEqual(config.auth_type, McpAuthType.COGNITO_CLIENT_CREDENTIALS)

    def test_cognito_client_credentials_requires_client_id_and_secret(self):
        with self.assertRaises(ValidationError):
            McpConfig(
                label="foo",
                endpoint_url="https://example.com/mcp",
                auth_type=McpAuthType.COGNITO_CLIENT_CREDENTIALS,
            )

    def test_none_requires_no_extra_fields(self):
        config = McpConfig(
            label="foo",
            endpoint_url="https://example.com/mcp",
            auth_type=McpAuthType.NONE,
        )
        self.assertIsNone(config.client_id)
        self.assertIsNone(config.bearer_token)

    def test_none_rejects_extra_fields(self):
        with self.assertRaises(ValidationError):
            McpConfig(
                label="foo",
                endpoint_url="https://example.com/mcp",
                auth_type=McpAuthType.NONE,
                bearer_token="should-not-be-here",
            )

    def test_bearer_token_requires_token(self):
        with self.assertRaises(ValidationError):
            McpConfig(
                label="foo",
                endpoint_url="https://example.com/mcp",
                auth_type=McpAuthType.BEARER_TOKEN,
            )

    def test_bearer_token_valid(self):
        config = McpConfig(
            label="foo",
            endpoint_url="https://example.com/mcp",
            auth_type=McpAuthType.BEARER_TOKEN,
            bearer_token="token-1",
        )
        self.assertEqual(config.bearer_token, "token-1")

    def test_basic_auth_requires_username_and_token(self):
        with self.assertRaises(ValidationError):
            McpConfig(
                label="foo",
                endpoint_url="https://example.com/mcp",
                auth_type=McpAuthType.BASIC_AUTH,
                username="user-1",
            )

    def test_basic_auth_valid(self):
        config = McpConfig(
            label="foo",
            endpoint_url="https://example.com/mcp",
            auth_type=McpAuthType.BASIC_AUTH,
            username="user-1",
            basic_auth_token="token-1",
        )
        self.assertEqual(config.username, "user-1")
        self.assertEqual(config.basic_auth_token, "token-1")


if __name__ == "__main__":
    unittest.main()
