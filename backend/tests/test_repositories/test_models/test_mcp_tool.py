import sys

sys.path.append(".")
import json
import unittest
from unittest.mock import patch

from app.repositories.models.custom_bot import AgentModel, McpConfigModel, McpToolModel
from app.routes.schemas.bot import AgentInput, McpConfig, McpTool


class TestMcpToolModel(unittest.TestCase):
    @patch("app.repositories.models.custom_bot.store_api_key_to_secret_manager")
    def test_from_agent_input_stores_all_secrets_as_one_json_blob(self, mock_store):
        mock_store.return_value = (
            "arn:aws:secretsmanager:ap-northeast-1:111111111111:secret:mcp/user1/bot1"
        )

        agent_input = AgentInput(
            tools=[
                McpTool(
                    tool_type="mcp",
                    name="mcp",
                    description="MCP knowledge search",
                    mcpServers=[
                        McpConfig(
                            label="powersort",
                            endpoint_url="https://example.com/powersort",
                            client_id="client-1",
                            client_secret="secret-1",
                        ),
                        McpConfig(
                            label="dbplayer",
                            endpoint_url="https://example.com/dbplayer",
                            client_id="client-2",
                            client_secret="secret-2",
                        ),
                    ],
                )
            ]
        )

        agent_model = AgentModel.from_agent_input(agent_input, "user1", "bot1")

        self.assertEqual(len(agent_model.tools), 1)
        tool = agent_model.tools[0]
        self.assertIsInstance(tool, McpToolModel)
        self.assertEqual(len(tool.mcpServers), 2)
        self.assertEqual(tool.mcpServers[0].label, "powersort")
        self.assertEqual(tool.mcpServers[0].client_secret, "secret-1")
        self.assertEqual(tool.mcpServers[1].label, "dbplayer")
        self.assertEqual(tool.mcpServers[1].client_secret, "secret-2")
        self.assertEqual(
            tool.secret_arn,
            "arn:aws:secretsmanager:ap-northeast-1:111111111111:secret:mcp/user1/bot1",
        )
        mock_store.assert_called_once_with(
            "user1",
            "bot1",
            "mcp",
            json.dumps({"powersort": "secret-1", "dbplayer": "secret-2"}),
        )

    @patch("app.repositories.models.custom_bot.store_api_key_to_secret_manager")
    def test_from_agent_input_with_no_servers_skips_secret_storage(self, mock_store):
        agent_input = AgentInput(
            tools=[
                McpTool(
                    tool_type="mcp",
                    name="mcp",
                    description="MCP knowledge search",
                    mcpServers=[],
                )
            ]
        )

        agent_model = AgentModel.from_agent_input(agent_input, "user1", "bot1")

        tool = agent_model.tools[0]
        self.assertEqual(tool.mcpServers, [])
        self.assertIsNone(tool.secret_arn)
        mock_store.assert_not_called()

    @patch("app.repositories.models.custom_bot.get_api_key_from_secret_manager")
    def test_to_agent_round_trips_all_servers(self, mock_get_secret):
        mock_get_secret.return_value = json.dumps(
            {"powersort": "secret-1", "dbplayer": "secret-2"}
        )

        agent_model = AgentModel(
            tools=[
                McpToolModel(
                    tool_type="mcp",
                    name="mcp",
                    description="MCP knowledge search",
                    secret_arn="arn:aws:secretsmanager:ap-northeast-1:111111111111:secret:mcp/user1/bot1",
                    mcpServers=[
                        McpConfigModel(
                            label="powersort",
                            endpoint_url="https://example.com/powersort",
                            client_id="client-1",
                            client_secret="",
                        ),
                        McpConfigModel(
                            label="dbplayer",
                            endpoint_url="https://example.com/dbplayer",
                            client_id="client-2",
                            client_secret="",
                        ),
                    ],
                )
            ]
        )

        agent = agent_model.to_agent()

        self.assertEqual(len(agent.tools), 1)
        tool = agent.tools[0]
        self.assertEqual(tool.tool_type, "mcp")
        self.assertEqual(len(tool.mcpServers), 2)
        self.assertEqual(tool.mcpServers[0].label, "powersort")
        self.assertEqual(tool.mcpServers[0].client_secret, "secret-1")
        self.assertEqual(tool.mcpServers[1].label, "dbplayer")
        self.assertEqual(tool.mcpServers[1].client_secret, "secret-2")

    @patch("app.repositories.models.custom_bot.store_api_key_to_secret_manager")
    def test_from_agent_input_stores_bearer_token_secret(self, mock_store):
        mock_store.return_value = (
            "arn:aws:secretsmanager:ap-northeast-1:111111111111:secret:mcp/user1/bot1"
        )

        agent_input = AgentInput(
            tools=[
                McpTool(
                    tool_type="mcp",
                    name="mcp",
                    description="MCP knowledge search",
                    mcpServers=[
                        McpConfig(
                            label="rovo",
                            endpoint_url="https://mcp.atlassian.com/v1/mcp",
                            auth_type="bearer_token",
                            bearer_token="rovo-token-1",
                        ),
                    ],
                )
            ]
        )

        agent_model = AgentModel.from_agent_input(agent_input, "user1", "bot1")

        tool = agent_model.tools[0]
        self.assertEqual(tool.mcpServers[0].bearer_token, "rovo-token-1")
        mock_store.assert_called_once_with(
            "user1", "bot1", "mcp", json.dumps({"rovo": "rovo-token-1"})
        )

    @patch("app.repositories.models.custom_bot.store_api_key_to_secret_manager")
    def test_from_agent_input_stores_basic_auth_secret(self, mock_store):
        mock_store.return_value = (
            "arn:aws:secretsmanager:ap-northeast-1:111111111111:secret:mcp/user1/bot1"
        )

        agent_input = AgentInput(
            tools=[
                McpTool(
                    tool_type="mcp",
                    name="mcp",
                    description="MCP knowledge search",
                    mcpServers=[
                        McpConfig(
                            label="rovo",
                            endpoint_url="https://mcp.atlassian.com/v1/mcp",
                            auth_type="basic_auth",
                            username="me@example.com",
                            basic_auth_token="api-token-1",
                        ),
                    ],
                )
            ]
        )

        agent_model = AgentModel.from_agent_input(agent_input, "user1", "bot1")

        tool = agent_model.tools[0]
        self.assertEqual(tool.mcpServers[0].username, "me@example.com")
        self.assertEqual(tool.mcpServers[0].basic_auth_token, "api-token-1")
        mock_store.assert_called_once_with(
            "user1", "bot1", "mcp", json.dumps({"rovo": "api-token-1"})
        )

    @patch("app.repositories.models.custom_bot.store_api_key_to_secret_manager")
    def test_from_agent_input_none_auth_type_skips_secret_storage(self, mock_store):
        agent_input = AgentInput(
            tools=[
                McpTool(
                    tool_type="mcp",
                    name="mcp",
                    description="MCP knowledge search",
                    mcpServers=[
                        McpConfig(
                            label="public",
                            endpoint_url="https://example.com/mcp",
                            auth_type="none",
                        ),
                    ],
                )
            ]
        )

        agent_model = AgentModel.from_agent_input(agent_input, "user1", "bot1")

        tool = agent_model.tools[0]
        self.assertIsNone(tool.secret_arn)
        mock_store.assert_not_called()

    @patch("app.repositories.models.custom_bot.store_api_key_to_secret_manager")
    def test_from_agent_input_mixed_auth_types_only_stores_needed_secrets(
        self, mock_store
    ):
        mock_store.return_value = (
            "arn:aws:secretsmanager:ap-northeast-1:111111111111:secret:mcp/user1/bot1"
        )

        agent_input = AgentInput(
            tools=[
                McpTool(
                    tool_type="mcp",
                    name="mcp",
                    description="MCP knowledge search",
                    mcpServers=[
                        McpConfig(
                            label="public",
                            endpoint_url="https://example.com/mcp",
                            auth_type="none",
                        ),
                        McpConfig(
                            label="rovo",
                            endpoint_url="https://mcp.atlassian.com/v1/mcp",
                            auth_type="bearer_token",
                            bearer_token="rovo-token-1",
                        ),
                    ],
                )
            ]
        )

        AgentModel.from_agent_input(agent_input, "user1", "bot1")

        mock_store.assert_called_once_with(
            "user1", "bot1", "mcp", json.dumps({"rovo": "rovo-token-1"})
        )

    @patch("app.repositories.models.custom_bot.get_api_key_from_secret_manager")
    def test_to_agent_round_trips_bearer_token(self, mock_get_secret):
        mock_get_secret.return_value = json.dumps({"rovo": "rovo-token-1"})

        agent_model = AgentModel(
            tools=[
                McpToolModel(
                    tool_type="mcp",
                    name="mcp",
                    description="MCP knowledge search",
                    secret_arn="arn:aws:secretsmanager:ap-northeast-1:111111111111:secret:mcp/user1/bot1",
                    mcpServers=[
                        McpConfigModel(
                            label="rovo",
                            endpoint_url="https://mcp.atlassian.com/v1/mcp",
                            auth_type="bearer_token",
                            bearer_token="",
                        ),
                    ],
                )
            ]
        )

        agent = agent_model.to_agent()

        self.assertEqual(agent.tools[0].mcpServers[0].bearer_token, "rovo-token-1")

    def test_to_agent_round_trips_none_auth_type_without_secret_lookup(self):
        agent_model = AgentModel(
            tools=[
                McpToolModel(
                    tool_type="mcp",
                    name="mcp",
                    description="MCP knowledge search",
                    secret_arn=None,
                    mcpServers=[
                        McpConfigModel(
                            label="public",
                            endpoint_url="https://example.com/mcp",
                            auth_type="none",
                        ),
                    ],
                )
            ]
        )

        agent = agent_model.to_agent()

        self.assertEqual(agent.tools[0].mcpServers[0].auth_type, "none")

    def test_repr_does_not_leak_client_secret(self):
        model = McpConfigModel(
            label="powersort",
            endpoint_url="https://example.com/mcp",
            client_id="client-1",
            client_secret="do-not-leak-this-secret",
        )

        self.assertNotIn("do-not-leak-this-secret", repr(model))
        self.assertNotIn("do-not-leak-this-secret", f"{model}")

    def test_model_dump_blanks_secret_fields_for_every_auth_type(self):
        bearer_model = McpConfigModel(
            label="rovo",
            endpoint_url="https://mcp.atlassian.com/v1/mcp",
            auth_type="bearer_token",
            bearer_token="do-not-leak-this-token",
        )
        self.assertEqual(bearer_model.model_dump()["bearer_token"], "")

        basic_model = McpConfigModel(
            label="rovo",
            endpoint_url="https://mcp.atlassian.com/v1/mcp",
            auth_type="basic_auth",
            username="me@example.com",
            basic_auth_token="do-not-leak-this-token",
        )
        self.assertEqual(basic_model.model_dump()["basic_auth_token"], "")
        self.assertEqual(basic_model.model_dump()["username"], "me@example.com")


if __name__ == "__main__":
    unittest.main()
