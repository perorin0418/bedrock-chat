import json
import logging
import os
import secrets

from botocore.exceptions import ClientError

from app.repositories.custom_bot import find_bot_by_id
from app.repositories.mcp_oauth_state import pop_mcp_oauth_state, save_mcp_oauth_state
from app.repositories.models.custom_bot import BotModel
from app.routes.schemas.bot import McpAuthType
from app.strands_integration.tools.mcp_oauth_flow import (
    build_authorization_url,
    discover_and_register,
    discover_oauth_metadata,
    exchange_code_for_tokens,
)
from app.strands_integration.tools.mcp_oauth_storage import SecretsManagerTokenStorage
from app.utils import get_api_key_from_secret_manager, store_api_key_to_secret_manager
from mcp.client.auth.oauth2 import PKCEParameters
from app.user import User

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

MCP_OAUTH_REDIRECT_URI = os.environ.get("MCP_OAUTH_REDIRECT_URI", "")

# Requesting `offline_access` is required for Atlassian (and most MCP OAuth
# servers) to actually issue a refresh_token alongside the access_token.
MCP_OAUTH_SCOPE = "offline_access"


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

    _tool, server = _find_mcp_tool_and_server(bot, label)
    if server.auth_type != McpAuthType.OAUTH:
        raise ValueError(f"MCP server '{label}' auth_type is not 'oauth'")

    oauth_metadata, client_info = await discover_and_register(
        server.endpoint_url, MCP_OAUTH_REDIRECT_URI
    )

    # Persist the full client_info (including any client_secret DCR issued)
    # immediately, before the user's browser round trip, so this
    # registration is never lost even if the user never completes consent.
    storage = SecretsManagerTokenStorage(
        user_id=bot.owner_user_id, bot_id=bot.id, label=label
    )
    await storage.set_client_info(client_info)

    pkce = PKCEParameters.generate()
    state = secrets.token_urlsafe(32)

    # Augment (never replace) whatever scope DCR's client_info already
    # carries -- dropping a provider-granted scope (e.g. Atlassian's
    # `read:jira-work`) here would trade a ~1h token expiry for a token
    # with no usable API scope at all.
    requested_scope = " ".join(
        dict.fromkeys(
            filter(None, (client_info.scope or "").split() + [MCP_OAUTH_SCOPE])
        )
    )

    authorization_url = build_authorization_url(
        oauth_metadata,
        client_info,
        redirect_uri=MCP_OAUTH_REDIRECT_URI,
        code_challenge=pkce.code_challenge,
        state=state,
        scope=requested_scope,
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
    token exchange, secret storage) must degrade to the clean
    `(bot_id, label, False)` failure tuple rather than propagating, since an
    uncaught exception here would otherwise hit `app/main.py`'s blanket
    exception handler and return a raw 500 with the exception message
    straight to this anonymous, unauthenticated caller."""
    state_item = pop_mcp_oauth_state(state)
    if not state_item:
        raise ValueError(f"Unknown or expired MCP OAuth state: {state}")

    if error or not code:
        return state_item.bot_id, state_item.label, False

    try:
        bot = find_bot_by_id(state_item.bot_id)
        _tool, server = _find_mcp_tool_and_server(bot, state_item.label)

        # Discovery only -- NOT discover_and_register: a second Dynamic
        # Client Registration on every callback would orphan a fresh OAuth
        # client each time, since the one from `start_mcp_oauth_authorize`
        # (with its client_secret) is already persisted below.
        oauth_metadata = await discover_oauth_metadata(server.endpoint_url)

        storage = SecretsManagerTokenStorage(
            user_id=bot.owner_user_id, bot_id=bot.id, label=state_item.label
        )
        client_info = await storage.get_client_info()
        if client_info is None:
            # Shouldn't normally happen: start_mcp_oauth_authorize always
            # persists client_info before saving state. Defensive fallback.
            raise ValueError(
                f"No persisted MCP OAuth client_info for bot '{bot.id}' "
                f"label '{state_item.label}'"
            )

        tokens = await exchange_code_for_tokens(
            oauth_metadata,
            client_info,
            code=code,
            code_verifier=state_item.code_verifier,
            redirect_uri=MCP_OAUTH_REDIRECT_URI,
        )

        await storage.set_tokens(tokens)
    except Exception:
        logger.exception(
            f"MCP OAuth callback failed for bot '{state_item.bot_id}' "
            f"label '{state_item.label}'"
        )
        return state_item.bot_id, state_item.label, False

    return state_item.bot_id, state_item.label, True


def disconnect_mcp_oauth(user: User, bot_id: str, label: str) -> None:
    """Remove one MCP server's stored oauth client_info/tokens so it must be
    reconnected (fresh DCR + consent) before it can be used again. The oauth
    Secrets Manager entry itself (and other labels' data in it) is kept."""
    bot = find_bot_by_id(bot_id)
    if not bot.is_owned_by_user(user):
        raise PermissionError(f"User {user.id} does not own bot {bot_id}")

    # Validate the label actually exists on the bot before touching Secrets Manager.
    _find_mcp_tool_and_server(bot, label)

    secret_name = f"mcp-oauth/{bot.owner_user_id}/{bot.id}"
    try:
        raw = get_api_key_from_secret_manager(secret_name)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            # Never connected / secret doesn't exist yet: nothing to disconnect.
            return
        raise

    blob = json.loads(raw or "{}")
    if label not in blob:
        return

    del blob[label]
    store_api_key_to_secret_manager(
        bot.owner_user_id, bot.id, "mcp-oauth", json.dumps(blob)
    )
