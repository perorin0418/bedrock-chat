import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, ".")
from app.repositories.models.custom_bot import McpToolModel
from app.repositories.models.custom_bot import _mcp_secret_field_name
from app.routes.schemas.bot import McpAuthType, McpConfig, McpTool


class TestMcpSecretFieldNameOauth(unittest.TestCase):
    def test_returns_none_for_oauth(self):
        # Regression test for the KeyError found in Task 1's review: this
        # function is a plain dict subscript, so every McpAuthType value
        # must have an entry or callers crash the moment that auth_type is
        # used (load_mcp_secrets / from_tool_input below).
        self.assertIsNone(_mcp_secret_field_name(McpAuthType.OAUTH))

    def test_from_tool_input_does_not_raise_for_oauth_only_server(self):
        tool = McpTool(
            tool_type="mcp",
            name="mcp",
            description="d",
            mcpServers=[
                McpConfig(
                    label="atlassian",
                    endpoint_url="https://mcp.atlassian.com/v1/mcp",
                    auth_type=McpAuthType.OAUTH,
                )
            ],
        )

        # Must not raise KeyError. No Secrets Manager call is expected since
        # an oauth-only server contributes no entry to `secrets_by_label`.
        model = McpToolModel.from_tool_input(tool, user_id="user-1", bot_id="bot-1")

        self.assertIsNone(model.secret_arn)


class TestMcpToolModelOauth(unittest.TestCase):
    def test_oauth_server_defaults_no_secret_arn(self):
        tool = McpToolModel(
            tool_type="mcp",
            name="mcp",
            description="d",
            mcpServers=[
                {
                    "label": "atlassian",
                    "endpoint_url": "https://mcp.atlassian.com/v1/mcp",
                    "auth_type": McpAuthType.OAUTH,
                }
            ],
        )
        self.assertIsNone(tool.oauth_secret_arn)
        self.assertFalse(tool.mcpServers[0].oauth_connected)

    @patch("app.repositories.models.custom_bot.is_mcp_oauth_connected")
    def test_oauth_connected_hydrated_from_secret(self, mock_is_connected):
        mock_is_connected.return_value = True
        tool = McpToolModel(
            tool_type="mcp",
            name="mcp",
            description="d",
            mcpServers=[
                {
                    "label": "atlassian",
                    "endpoint_url": "https://mcp.atlassian.com/v1/mcp",
                    "auth_type": McpAuthType.OAUTH,
                }
            ],
            oauth_secret_arn="arn:aws:secretsmanager:...",
        )
        self.assertTrue(tool.mcpServers[0].oauth_connected)
        mock_is_connected.assert_called_once_with(
            "arn:aws:secretsmanager:...", "atlassian"
        )

    @patch("app.repositories.models.custom_bot.is_mcp_oauth_connected")
    def test_to_agent_maps_oauth_connected(self, mock_is_connected):
        # Regression note: the brief's original version of this test set
        # `oauth_connected: True` directly in the input dict with no
        # `oauth_secret_arn`. But `hydrate_oauth_connected_status` always
        # recomputes `oauth_connected` from the real oauth secret (never
        # trusting a pre-supplied value), so with no ARN it is legitimately
        # recomputed to False via the real (unmocked)
        # `is_mcp_oauth_connected(None, ...)` -- that version of the test
        # failed for a reason unrelated to the `to_agent()` mapping it meant
        # to check. Mocking `is_mcp_oauth_connected` and supplying an ARN
        # here isolates the actual thing under test: that `to_agent()`'s
        # `McpConfig(...)` call forwards `oauth_connected=server.oauth_connected`.
        mock_is_connected.return_value = True
        tool = McpToolModel(
            tool_type="mcp",
            name="mcp",
            description="d",
            mcpServers=[
                {
                    "label": "atlassian",
                    "endpoint_url": "https://mcp.atlassian.com/v1/mcp",
                    "auth_type": McpAuthType.OAUTH,
                }
            ],
            oauth_secret_arn="arn:aws:secretsmanager:...",
        )
        from app.repositories.models.custom_bot import AgentModel

        agent = AgentModel(tools=[tool]).to_agent()
        self.assertTrue(agent.tools[0].mcpServers[0].oauth_connected)  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()
