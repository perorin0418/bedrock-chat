import logging
import os
import secrets

from app.repositories.custom_bot import find_bot_by_id, update_bot_mcp_oauth_secret_arn
from app.repositories.mcp_oauth_state import pop_mcp_oauth_state, save_mcp_oauth_state
from app.repositories.models.custom_bot import BotModel
from app.routes.schemas.bot import McpAuthType
from app.strands_integration.tools.mcp_oauth_flow import (
    build_authorization_url,
    discover_and_register,
    exchange_code_for_tokens,
)
from app.strands_integration.tools.mcp_oauth_storage import SecretsManagerTokenStorage
from mcp.client.auth.oauth2 import PKCEParameters
from mcp.shared.auth import OAuthClientInformationFull
from app.user import User

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

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


async def complete_mcp_oauth_callback(
    code: str | None, state: str, error: str | None = None
) -> tuple[str, str, bool]:
    """Complete the authorization-code exchange for the in-flight request
    identified by `state`. Returns `(bot_id, label, succeeded)` so the route
    can redirect the browser back to the right bot edit screen either way.

    This is invoked from the one unauthenticated route in this backend
    (`GET /mcp/oauth/callback`), reached directly by the OAuth provider's
    browser redirect. Only the "unknown/expired state" case (before we even
    know which bot/label this request belongs to) raises `ValueError` --
    the route handles that by redirecting to a generic fallback page. Every
    other failure once `state_item` is known (bot lookup, MCP discovery,
    token exchange, secret storage, updating the bot's oauth_secret_arn)
    must degrade to the clean `(bot_id, label, False)` failure tuple rather
    than propagating, since an uncaught exception here would otherwise hit
    `app/main.py`'s blanket exception handler and return a raw 500 with the
    exception message straight to this anonymous, unauthenticated caller."""
    state_item = pop_mcp_oauth_state(state)
    if not state_item:
        raise ValueError(f"Unknown or expired MCP OAuth state: {state}")

    if error or not code:
        return state_item.bot_id, state_item.label, False

    try:
        bot = find_bot_by_id(state_item.bot_id)
        tool, server = _find_mcp_tool_and_server(bot, state_item.label)

        oauth_metadata, _ = await discover_and_register(
            server.endpoint_url, MCP_OAUTH_REDIRECT_URI
        )

        tokens = await exchange_code_for_tokens(
            oauth_metadata,
            _client_info_from_state(state_item),
            code=code,
            code_verifier=state_item.code_verifier,
            redirect_uri=MCP_OAUTH_REDIRECT_URI,
        )

        storage = SecretsManagerTokenStorage(
            user_id=bot.owner_user_id,
            bot_id=bot.id,
            label=state_item.label,
            oauth_secret_arn=tool.oauth_secret_arn,
        )
        await storage.set_client_info(_client_info_from_state(state_item))
        await storage.set_tokens(tokens)
        assert storage.oauth_secret_arn is not None  # set_tokens() always assigns it

        tool_index = bot.agent.tools.index(tool)
        update_bot_mcp_oauth_secret_arn(
            owner_user_id=bot.owner_user_id,
            bot_id=bot.id,
            tool_index=tool_index,
            oauth_secret_arn=storage.oauth_secret_arn,
        )
    except Exception:
        logger.exception(
            f"MCP OAuth callback failed for bot '{state_item.bot_id}' "
            f"label '{state_item.label}'"
        )
        return state_item.bot_id, state_item.label, False

    return state_item.bot_id, state_item.label, True


def _client_info_from_state(state_item) -> OAuthClientInformationFull:
    """Reconstruct the minimal client_info needed for token exchange from
    what was saved alongside the PKCE state. NOTE: this does not carry a
    `client_secret` -- known limitation: if Atlassian's Dynamic Client
    Registration response ever returns a `client_secret` (i.e. registers a
    confidential client rather than a public PKCE-only client), it must
    also be persisted onto `McpOAuthState` and restored here, or token
    exchange for such servers will fail. Verified against Atlassian's actual
    DCR response before relying on this in production; today's assumption
    is "no client_secret => public client", which matches Atlassian's MCP
    OAuth server as observed during Task 5/7 implementation."""
    return OAuthClientInformationFull(
        redirect_uris=[MCP_OAUTH_REDIRECT_URI],  # type: ignore[list-item]
        client_id=state_item.client_id,
    )
