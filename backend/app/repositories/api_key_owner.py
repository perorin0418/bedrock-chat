import logging

from app.repositories.common import get_api_key_owner_table_client
from app.utils import get_current_time

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def bind_api_key_owner(api_key_id: str, user_id: str) -> None:
    """Bind an API key to the user who created it.

    Not best-effort: an API key without a bound owner cannot be rate-limited,
    so callers must treat a failure here as API key creation failure.
    """
    table = get_api_key_owner_table_client()
    table.put_item(
        Item={
            "ApiKeyId": api_key_id,
            "UserId": user_id,
            "CreateTime": get_current_time(),
        }
    )


def find_api_key_owner(api_key_id: str) -> str | None:
    """Return the bound user_id for `api_key_id`, or None if unbound
    (e.g. a key created before this feature existed)."""
    table = get_api_key_owner_table_client()
    response = table.get_item(Key={"ApiKeyId": api_key_id})
    item = response.get("Item")
    return item["UserId"] if item else None


def delete_api_key_owner(api_key_id: str) -> None:
    """Best-effort delete: cleanup must never block API key/bot deletion."""
    try:
        table = get_api_key_owner_table_client()
        table.delete_item(Key={"ApiKeyId": api_key_id})
    except Exception:
        logger.exception(f"Failed to delete api key owner binding for {api_key_id}.")
