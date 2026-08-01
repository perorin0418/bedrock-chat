"""
MCP server integration - token acquisition/caching and tool scoping.
"""

import logging
import os
import time
from contextlib import ExitStack, contextmanager

import requests
from app.repositories.models.custom_bot import BotModel, McpToolModel
from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp import MCPAgentTool, MCPClient

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

BEDROCK_REGION = os.environ.get("BEDROCK_REGION", "us-east-1")
COGNITO_MCP_AUTH_DOMAIN = f"knowledge-mcp-auth.auth.{BEDROCK_REGION}.amazoncognito.com"

# Lambda container in-memory cache: cache_key -> (access_token, expires_at)
_token_cache: dict[str, tuple[str, float]] = {}

TOKEN_REFRESH_MARGIN_SECONDS = 90
MCP_CONNECTION_TIMEOUT_SECONDS = 10


def get_mcp_bearer_token(
    client_id: str, client_secret: str, cognito_domain: str, cache_key: str
) -> str:
    """Get a Cognito client_credentials access token, using an in-memory cache.

    Args:
        client_id: Cognito app client ID
        client_secret: Cognito app client secret
        cognito_domain: Cognito hosted UI domain (e.g. "knowledge-mcp-auth.auth.ap-northeast-1.amazoncognito.com")
        cache_key: cache key that uniquely identifies this KB's credentials (the secret ARN)

    Returns:
        str: Bearer access token
    """
    cached = _token_cache.get(cache_key)
    if cached and cached[1] > time.time() + TOKEN_REFRESH_MARGIN_SECONDS:
        return cached[0]

    response = requests.post(
        f"https://{cognito_domain}/oauth2/token",
        auth=(client_id, client_secret),
        data={"grant_type": "client_credentials", "scope": "knowledge-mcp/invoke"},
        timeout=10,
    )
    response.raise_for_status()
    body = response.json()

    expires_at = time.time() + body["expires_in"]
    _token_cache[cache_key] = (body["access_token"], expires_at)

    return body["access_token"]


def _get_mcp_tool(bot: BotModel | None) -> McpToolModel | None:
    """Extract the bot's MCP tool configuration (all configured servers)."""
    if not bot or not bot.agent or not bot.agent.tools:
        return None

    for tool_config in bot.agent.tools:
        if tool_config.tool_type == "mcp":
            return tool_config

    return None


def _safe_close(client: MCPClient, label: str) -> None:
    try:
        client.__exit__(None, None, None)  # type: ignore[arg-type]
    except Exception as close_error:
        logger.error(f"Error closing MCP client for server '{label}': {close_error}")


@contextmanager
def mcp_tools_scope(bot: BotModel | None):
    """Open one MCP connection per configured server, scoped to a single chat turn,
    and yield the combined list of tools with names prefixed by each server's label.

    Each server is independent: a connection/token/list_tools failure for one
    server only drops that server's tools (logged), other servers' tools and
    the chat continue normally.
    """
    mcp_tool = _get_mcp_tool(bot)
    if not mcp_tool or not mcp_tool.mcpServers:
        yield []
        return

    combined_tools: list[MCPAgentTool] = []
    with ExitStack() as stack:
        for server in mcp_tool.mcpServers:
            try:
                token = get_mcp_bearer_token(
                    server.client_id,
                    server.client_secret,
                    COGNITO_MCP_AUTH_DOMAIN,
                    f"{mcp_tool.secret_arn}:{server.label}",
                )
                client = MCPClient(
                    lambda: streamablehttp_client(
                        server.endpoint_url,
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=MCP_CONNECTION_TIMEOUT_SECONDS,
                    ),
                    startup_timeout=MCP_CONNECTION_TIMEOUT_SECONDS,
                )
                client.__enter__()
            except Exception as e:
                logger.error(
                    f"MCP server '{server.label}' connection failed, "
                    f"skipping its tools: {e}"
                )
                continue

            stack.callback(_safe_close, client, server.label)

            try:
                server_tools = client.list_tools_sync()
            except Exception as e:
                logger.error(
                    f"MCP server '{server.label}' list_tools failed, "
                    f"skipping its tools: {e}"
                )
                continue

            for tool in server_tools:
                tool.mcp_tool.name = f"{server.label}_{tool.mcp_tool.name}"
            combined_tools.extend(server_tools)

        yield combined_tools
