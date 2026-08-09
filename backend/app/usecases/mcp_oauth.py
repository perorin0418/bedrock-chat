import os
import secrets

from app.repositories.custom_bot import find_bot_by_id
from app.repositories.mcp_oauth_state import save_mcp_oauth_state
from app.repositories.models.custom_bot import BotModel
from app.routes.schemas.bot import McpAuthType
from app.strands_integration.tools.mcp_oauth_flow import (
    build_authorization_url,
    discover_and_register,
)
from mcp.client.auth.oauth2 import PKCEParameters
from app.user import User

MCP_OAUTH_REDIRECT_URI = os.environ.get("MCP_OAUTH_REDIRECT_URI", "")


def _find_mcp_tool_and_server(bot: BotModel, label: str):
    for tool in bot.agent.tools:
        if getattr(tool, "tool_type", None) != "mcp":
            continue
        for server in getattr(tool, "mcpServers", []):
            if server.label == label:
                return tool, server
    raise ValueError(f"MCP server '{label}' not found on bot '{bot.id}'")


async def start_mcp_oauth_authorize(user: User, bot_id: str, label: str) -> str:
    """Kick off the OAuth authorization-code flow for one bot's MCP server.
    Returns the URL the bot owner's browser should navigate to."""
    bot = find_bot_by_id(bot_id)
    if not bot.is_owned_by_user(user):
        raise PermissionError(f"User {user.id} does not own bot {bot_id}")

    tool, server = _find_mcp_tool_and_server(bot, label)
    if server.auth_type != McpAuthType.OAUTH:
        raise ValueError(f"MCP server '{label}' auth_type is not 'oauth'")

    oauth_metadata, client_info = await discover_and_register(
        server.endpoint_url, MCP_OAUTH_REDIRECT_URI
    )

    pkce = PKCEParameters.generate()
    state = secrets.token_urlsafe(32)

    authorization_url = build_authorization_url(
        oauth_metadata,
        client_info,
        redirect_uri=MCP_OAUTH_REDIRECT_URI,
        code_challenge=pkce.code_challenge,
        state=state,
        scope=client_info.scope,
    )

    save_mcp_oauth_state(
        state=state,
        bot_id=bot_id,
        label=label,
        owner_user_id=bot.owner_user_id,
        code_verifier=pkce.code_verifier,
        client_id=client_info.client_id or "",
    )

    return authorization_url
