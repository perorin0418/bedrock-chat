"""
MCP server integration - token acquisition/caching and tool scoping.
"""

import logging
import os
import time
from contextlib import contextmanager

import requests
from app.repositories.models.custom_bot import BotModel
from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp import MCPClient

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

BEDROCK_REGION = os.environ.get("BEDROCK_REGION", "us-east-1")
COGNITO_MCP_AUTH_DOMAIN = f"knowledge-mcp-auth.auth.{BEDROCK_REGION}.amazoncognito.com"

# Lambda container in-memory cache: cache_key -> (access_token, expires_at)
_token_cache: dict[str, tuple[str, float]] = {}

TOKEN_REFRESH_MARGIN_SECONDS = 90


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


def _get_mcp_tool_config(bot: BotModel | None):
    """Extract MCP tool configuration from bot."""
    if not bot or not bot.agent or not bot.agent.tools:
        return None

    for tool_config in bot.agent.tools:
        if tool_config.tool_type == "mcp" and tool_config.mcpConfig:
            return tool_config.mcpConfig

    return None


@contextmanager
def mcp_tools_scope(bot: BotModel | None):
    """Open an MCP connection scoped to a single chat turn and yield its tools.

    Yields an empty list when the bot has no MCP tool configured, or when
    the connection/token fetch fails (degrade gracefully, keep the chat working).
    """
    config = _get_mcp_tool_config(bot)
    if not config:
        yield []
        return

    try:
        token = get_mcp_bearer_token(
            config.client_id,
            config.client_secret,
            COGNITO_MCP_AUTH_DOMAIN,
            config.secret_arn,
        )
        client = MCPClient(
            lambda: streamablehttp_client(
                config.endpoint_url,
                headers={"Authorization": f"Bearer {token}"},
            )
        )
        client.__enter__()
    except Exception as e:
        logger.error(f"MCP connection failed, falling back without MCP tools: {e}")
        yield []
        return

    try:
        tools = client.list_tools_sync()
    except Exception as e:
        logger.error(f"MCP connection failed, falling back without MCP tools: {e}")
        client.__exit__(None, None, None)
        yield []
        return

    try:
        yield tools
    finally:
        client.__exit__(None, None, None)
