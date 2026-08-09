import json
import logging

from app.utils import get_api_key_from_secret_manager

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def get_mcp_oauth_blob(oauth_secret_arn: str) -> dict[str, dict]:
    """Load the oauth client_info/tokens blob for all of a bot's oauth-type
    MCP servers, keyed by server label:
    `{label: {"client_info": {...}, "tokens": {...}}}`.
    """
    raw = get_api_key_from_secret_manager(oauth_secret_arn)
    return json.loads(raw) if raw else {}


def is_mcp_oauth_connected(oauth_secret_arn: str | None, label: str) -> bool:
    """Whether `label` has a stored access token (best-effort: any Secrets
    Manager error is treated as "not connected" rather than raised, since
    this is used to render a read-only status flag)."""
    if not oauth_secret_arn:
        return False
    try:
        blob = get_mcp_oauth_blob(oauth_secret_arn)
    except Exception:
        logger.exception(f"Failed to check MCP oauth connection status for '{label}'")
        return False
    return bool(blob.get(label, {}).get("tokens"))
