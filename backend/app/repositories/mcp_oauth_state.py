import logging
from dataclasses import dataclass

from app.repositories.common import get_mcp_oauth_state_table_client
from app.utils import get_current_time

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@dataclass
class McpOAuthStateItem:
    bot_id: str
    label: str
    owner_user_id: str
    code_verifier: str
    client_id: str


def save_mcp_oauth_state(
    state: str,
    bot_id: str,
    label: str,
    owner_user_id: str,
    code_verifier: str,
    client_id: str,
    ttl_seconds: int = 600,
) -> None:
    """Store the PKCE/CSRF state for one in-flight MCP OAuth authorization
    request. `ttl_seconds` bounds how long the bot owner has to complete the
    Atlassian consent screen before the round trip must be restarted."""
    table = get_mcp_oauth_state_table_client()
    table.put_item(
        Item={
            "State": state,
            "BotId": bot_id,
            "Label": label,
            "OwnerUserId": owner_user_id,
            "CodeVerifier": code_verifier,
            "ClientId": client_id,
            "expire": get_current_time() // 1000 + ttl_seconds,
        }
    )


def pop_mcp_oauth_state(state: str) -> McpOAuthStateItem | None:
    """Return and delete the stored state (single-use: the same `state`
    value can't be replayed). Returns None if not found or already expired.
    DynamoDB TTL deletion is best-effort/eventual (can lag by hours), so
    `expire` is also checked here explicitly rather than relying purely on
    TTL: an expired-but-not-yet-deleted item is deleted (cleanup) and
    treated as missing."""
    table = get_mcp_oauth_state_table_client()
    response = table.get_item(Key={"State": state})
    item = response.get("Item")
    if not item:
        return None

    table.delete_item(Key={"State": state})

    expire = item.get("expire")
    if expire is not None and expire < get_current_time() // 1000:
        return None

    return McpOAuthStateItem(
        bot_id=item["BotId"],
        label=item["Label"],
        owner_user_id=item["OwnerUserId"],
        code_verifier=item["CodeVerifier"],
        client_id=item["ClientId"],
    )
