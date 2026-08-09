import json
import logging

from app.utils import get_api_key_from_secret_manager

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _mcp_oauth_secret_name(user_id: str, bot_id: str) -> str:
    """Deterministic Secrets Manager secret name for a bot's oauth-type MCP
    servers, shared across all of that bot's oauth servers (keyed by label
    inside the blob). `get_api_key_from_secret_manager`'s `SecretId` param
    accepts either a real ARN or a plain secret name, so there is no need to
    look up or persist an actual ARN anywhere."""
    return f"mcp-oauth/{user_id}/{bot_id}"


def get_mcp_oauth_blob(user_id: str, bot_id: str) -> dict[str, dict]:
    """Load the oauth client_info/tokens blob for all of a bot's oauth-type
    MCP servers, keyed by server label:
    `{label: {"client_info": {...}, "tokens": {...}}}`.
    """
    secret_name = _mcp_oauth_secret_name(user_id, bot_id)
    raw = get_api_key_from_secret_manager(secret_name)
    return json.loads(raw) if raw else {}


def is_mcp_oauth_connected(user_id: str, bot_id: str, label: str) -> bool:
    """Whether `label` has a stored access token (best-effort: any Secrets
    Manager error is treated as "not connected" rather than raised, since
    this is used to render a read-only status flag)."""
    try:
        blob = get_mcp_oauth_blob(user_id, bot_id)
    except Exception:
        logger.exception(f"Failed to check MCP oauth connection status for '{label}'")
        return False
    return bool(blob.get(label, {}).get("tokens"))
