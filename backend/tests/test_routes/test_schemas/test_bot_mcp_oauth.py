import sys
import unittest

sys.path.insert(0, ".")
from app.routes.schemas.bot import McpAuthType, McpConfig


class TestMcpConfigOauth(unittest.TestCase):
    def test_oauth_auth_type_requires_no_fields(self):
        config = McpConfig(
            label="atlassian",
            endpoint_url="https://mcp.atlassian.com/v1/mcp",
            auth_type=McpAuthType.OAUTH,
        )
        self.assertEqual(config.auth_type, McpAuthType.OAUTH)

    def test_oauth_config_defaults_oauth_connected_false(self):
        config = McpConfig(
            label="atlassian",
            endpoint_url="https://mcp.atlassian.com/v1/mcp",
            auth_type=McpAuthType.OAUTH,
        )
        self.assertFalse(config.oauth_connected)

    def test_oauth_rejects_other_auth_fields(self):
        with self.assertRaises(ValueError):
            McpConfig(
                label="atlassian",
                endpoint_url="https://mcp.atlassian.com/v1/mcp",
                auth_type=McpAuthType.OAUTH,
                api_key="should-not-be-set",
            )


if __name__ == "__main__":
    unittest.main()
