"""
Static placeholder for the MCP tool catalog entry.

This tool is never actually invoked: when a bot has an `mcp` tool configured,
the real tools are fetched live from the MCP server via
`app.strands_integration.tools.mcp_tools.mcp_tools_scope` and passed to the
Agent as `extra_tools`. This placeholder exists only so `mcp` shows up with a
name/description in the bot-creation "available tools" list.
"""

from strands import tool
from strands.types.tools import AgentTool as StrandsAgentTool


def create_mcp_placeholder_tool() -> StrandsAgentTool:
    @tool
    def mcp() -> dict:
        """
        Connect to an MCP server configured for this bot to search its knowledge base.
        """
        return {
            "status": "error",
            "content": [
                {
                    "text": "This is a placeholder tool and should never be invoked directly."
                }
            ],
        }

    return mcp
