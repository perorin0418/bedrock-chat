import sys

sys.path.append(".")
import unittest
from unittest.mock import patch

from app.repositories.models.custom_bot import AgentModel, McpConfigModel, McpToolModel
from app.routes.schemas.bot import AgentInput, McpConfig, McpTool


class TestMcpToolModel(unittest.TestCase):
    @patch("app.repositories.models.custom_bot.store_api_key_to_secret_manager")
    def test_from_agent_input_stores_secret(self, mock_store):
        mock_store.return_value = (
            "arn:aws:secretsmanager:ap-northeast-1:111111111111:secret:mcp/user1/bot1"
        )

        agent_input = AgentInput(
            tools=[
                McpTool(
                    tool_type="mcp",
                    name="mcp",
                    description="MCP knowledge search",
                    mcpConfig=McpConfig(
                        endpoint_url="https://example.com/mcp",
                        client_id="client-1",
                        client_secret="s3cr3t",
                    ),
                )
            ]
        )

        agent_model = AgentModel.from_agent_input(agent_input, "user1", "bot1")

        self.assertEqual(len(agent_model.tools), 1)
        tool = agent_model.tools[0]
        self.assertIsInstance(tool, McpToolModel)
        self.assertEqual(tool.mcpConfig.endpoint_url, "https://example.com/mcp")
        self.assertEqual(tool.mcpConfig.client_id, "client-1")
        self.assertEqual(tool.mcpConfig.client_secret, "s3cr3t")
        self.assertEqual(
            tool.mcpConfig.secret_arn,
            "arn:aws:secretsmanager:ap-northeast-1:111111111111:secret:mcp/user1/bot1",
        )
        mock_store.assert_called_once_with("user1", "bot1", "mcp", "s3cr3t")

    @patch("app.repositories.models.custom_bot.get_api_key_from_secret_manager")
    def test_to_agent_round_trips_config(self, mock_get_secret):
        mock_get_secret.return_value = "s3cr3t"

        agent_model = AgentModel(
            tools=[
                McpToolModel(
                    tool_type="mcp",
                    name="mcp",
                    description="MCP knowledge search",
                    mcpConfig=McpConfigModel(
                        endpoint_url="https://example.com/mcp",
                        client_id="client-1",
                        secret_arn="arn:aws:secretsmanager:ap-northeast-1:111111111111:secret:mcp/user1/bot1",
                        client_secret="s3cr3t",
                    ),
                )
            ]
        )

        agent = agent_model.to_agent()

        self.assertEqual(len(agent.tools), 1)
        tool = agent.tools[0]
        self.assertEqual(tool.tool_type, "mcp")
        self.assertEqual(tool.mcpConfig.endpoint_url, "https://example.com/mcp")
        self.assertEqual(tool.mcpConfig.client_secret, "s3cr3t")

    def test_repr_does_not_leak_client_secret(self):
        model = McpConfigModel(
            endpoint_url="https://example.com/mcp",
            client_id="client-1",
            secret_arn="arn:aws:secretsmanager:ap-northeast-1:111111111111:secret:mcp/user1/bot1",
            client_secret="do-not-leak-this-secret",
        )

        self.assertNotIn("do-not-leak-this-secret", repr(model))
        self.assertNotIn("do-not-leak-this-secret", f"{model}")


if __name__ == "__main__":
    unittest.main()
