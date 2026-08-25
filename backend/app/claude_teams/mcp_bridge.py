"""Bridges bedrock-chat's existing strands-based tools (built-in tools like
internet search/knowledge search, plus a bot's configured external MCP
servers) into an in-process MCP server that the Claude Code CLI subprocess
(via `claude-agent-sdk`) can call.

Why bridge through strands instead of talking to `ClaudeAgentOptions.mcp_servers`
directly: that option only accepts server *launch* configs (stdio/SSE/HTTP
connection info), with no way to plug in a live `httpx.Auth` object the way
strands' `MCPClient` supports. Bedrock-chat's OAuth-type MCP servers need
that live, refreshable auth. Reusing `mcp_tools_scope` (already handles
connecting, auth, and per-server failure isolation) and wrapping the
resulting tools as `@tool`-decorated functions keeps that logic in one
place.
"""

import contextlib
import logging
from typing import Any

from app.repositories.models.custom_bot import BotModel
from app.routes.schemas.conversation import type_model_name
from app.strands_integration.tools.mcp_tools import mcp_tools_scope
from app.strands_integration.utils import get_strands_tools
from claude_agent_sdk import create_sdk_mcp_server, tool as sdk_tool

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

BEDROCK_CHAT_MCP_SERVER_NAME = "bedrock_chat_tools"


def list_allowed_tool_names(server_name: str, strands_tools: list) -> list[str]:
    """The `ClaudeAgentOptions.allowed_tools` entries for every tool in
    `strands_tools`, in the `mcp__{server_name}__{tool_name}` form the CLI
    uses for MCP-provided tools."""
    return [f"mcp__{server_name}__{t.tool_name}" for t in strands_tools]


def _wrap_strands_tool_as_sdk_tool(strands_agent_tool: Any):
    """Wrap one strands `AgentTool` as a `claude_agent_sdk` in-process MCP
    tool. Uses a permissive `{}` (any-shape dict) input schema since the
    strands tool already validates/handles its own arguments; the SDK's
    `@tool` decorator only needs *a* schema, not a strict one, to register
    the tool."""

    async def _handler(args: dict) -> dict:
        result_content: list[dict] = []
        status = "success"
        async for tool_result in strands_agent_tool.invoke_async(
            tool_use={
                "toolUseId": "claude-teams-bridge",
                "name": strands_agent_tool.tool_name,
                "input": args,
            },
            invocation_state={},
        ):
            status = tool_result.get("status", status)
            for content in tool_result.get("content", []):
                if "text" in content:
                    result_content.append({"type": "text", "text": content["text"]})
                elif "json" in content:
                    import json as _json

                    result_content.append(
                        {"type": "text", "text": _json.dumps(content["json"])}
                    )
        if not result_content:
            result_content = [{"type": "text", "text": ""}]
        return {"content": result_content, "is_error": status == "error"}

    return sdk_tool(
        strands_agent_tool.tool_name,
        f"Bedrock Chat tool: {strands_agent_tool.tool_name}",
        {},  # permissive schema; the wrapped strands tool validates its own input
    )(_handler)


def build_claude_teams_mcp_servers(
    bot: BotModel | None, model_name: type_model_name
) -> tuple[dict[str, Any], list[str], contextlib.AbstractContextManager]:
    """Return (mcp_servers dict for ClaudeAgentOptions, the matching
    allowed_tools list, a context manager to close when the chat turn
    finishes).

    All of a bot's built-in tools and external MCP server tools (already
    fetched live via `mcp_tools_scope`) are combined into a single
    in-process MCP server named `bedrock_chat_tools`.
    """
    stack = contextlib.ExitStack()
    mcp_tools = stack.enter_context(mcp_tools_scope(bot))
    builtin_tools = get_strands_tools(bot, model_name)

    all_tools = [*builtin_tools, *mcp_tools]

    if not all_tools:
        return {}, [], stack

    sdk_tools = [_wrap_strands_tool_as_sdk_tool(t) for t in all_tools]
    allowed_tools = list_allowed_tool_names(BEDROCK_CHAT_MCP_SERVER_NAME, all_tools)

    server = create_sdk_mcp_server(
        name=BEDROCK_CHAT_MCP_SERVER_NAME,
        tools=sdk_tools,
    )
    return {BEDROCK_CHAT_MCP_SERVER_NAME: server}, allowed_tools, stack
