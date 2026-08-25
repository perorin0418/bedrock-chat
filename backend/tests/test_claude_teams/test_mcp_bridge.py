import asyncio
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")
from app.claude_teams.mcp_bridge import (
    build_claude_teams_mcp_servers,
    list_allowed_tool_names,
)


class _FakeStrandsTool:
    def __init__(self, tool_name: str):
        self.tool_name = tool_name

    async def invoke_async(self, tool_use, invocation_state):
        yield {
            "status": "success",
            "content": [{"text": f"result for {self.tool_name}"}],
        }


class TestMcpBridge(unittest.TestCase):
    def test_list_allowed_tool_names_prefixes_with_mcp_server(self):
        tools = [_FakeStrandsTool("internet_search"), _FakeStrandsTool("bedrock_agent")]

        names = list_allowed_tool_names("bedrock_chat_tools", tools)

        self.assertEqual(
            names,
            [
                "mcp__bedrock_chat_tools__internet_search",
                "mcp__bedrock_chat_tools__bedrock_agent",
            ],
        )

    @patch("app.claude_teams.mcp_bridge.mcp_tools_scope")
    @patch("app.claude_teams.mcp_bridge.get_strands_tools")
    def test_build_claude_teams_mcp_servers_wraps_all_tools_into_one_server(
        self, mock_get_strands_tools, mock_mcp_tools_scope
    ):
        mock_get_strands_tools.return_value = [_FakeStrandsTool("internet_search")]
        mock_mcp_tools_scope.return_value.__enter__.return_value = [
            _FakeStrandsTool("atlassian_search")
        ]
        mock_mcp_tools_scope.return_value.__exit__.return_value = False

        mcp_servers, allowed_tools, cleanup = build_claude_teams_mcp_servers(
            bot=None, model_name="claude-teams-sonnet"
        )

        self.assertIn("bedrock_chat_tools", mcp_servers)
        self.assertEqual(
            allowed_tools,
            [
                "mcp__bedrock_chat_tools__internet_search",
                "mcp__bedrock_chat_tools__atlassian_search",
            ],
        )
        cleanup.__exit__(None, None, None)


if __name__ == "__main__":
    unittest.main()
