"""DynamoDB-backed metadata repository for the Claude Teams OAuth token pool.

The actual OAuth token strings live in Secrets Manager
(`claude-teams-token/{token_id}`, see `token_secrets.py`), never in this
table. This module only manages: which tokens exist, whether each is
enabled, its cooldown state, and round-robin selection bookkeeping
(`last_used_at`).
"""

import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from ulid import ULID

from app.repositories.common import get_claude_teams_token_table_client
from app.utils import get_current_time

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@dataclass
class ClaudeTeamsTokenItem:
    token_id: str
    display_name: str
    enabled: bool
    created_at: int
    cooldown_until: int | None = None
    last_used_at: int | None = None


def _item_to_model(item: dict) -> ClaudeTeamsTokenItem:
    return ClaudeTeamsTokenItem(
        token_id=item["TokenId"],
        display_name=item["DisplayName"],
        enabled=bool(item["Enabled"]),
        created_at=int(item["CreatedAt"]),
        cooldown_until=(
            int(item["CooldownUntil"]) if "CooldownUntil" in item else None
        ),
        last_used_at=(int(item["LastUsedAt"]) if "LastUsedAt" in item else None),
    )


def create_token(display_name: str) -> ClaudeTeamsTokenItem:
    """Create a new token pool entry (metadata only). Caller is responsible
    for separately storing the actual token string in Secrets Manager
    (see `token_secrets.store_claude_teams_token`)."""
    table = get_claude_teams_token_table_client()
    token_id = str(ULID())
    now_ms = get_current_time()
    table.put_item(
        Item={
            "TokenId": token_id,
            "DisplayName": display_name,
            "Enabled": True,
            "CreatedAt": now_ms,
        }
    )
    return ClaudeTeamsTokenItem(
        token_id=token_id,
        display_name=display_name,
        enabled=True,
        created_at=now_ms,
    )


def list_tokens() -> list[ClaudeTeamsTokenItem]:
    """Return all registered tokens (metadata only), unordered."""
    table = get_claude_teams_token_table_client()
    items: list[dict] = []
    response = table.scan()
    items.extend(response.get("Items", []))
    while "LastEvaluatedKey" in response:
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))
    return [_item_to_model(item) for item in items]


def set_enabled(token_id: str, enabled: bool) -> None:
    table = get_claude_teams_token_table_client()
    table.update_item(
        Key={"TokenId": token_id},
        UpdateExpression="SET Enabled = :enabled",
        ExpressionAttributeValues={":enabled": enabled},
    )


def disable_token(token_id: str) -> None:
    """Permanently disable a token (e.g. after an authentication_failed /
    oauth_org_not_allowed error, meaning the token itself is invalid)."""
    set_enabled(token_id, False)


def set_cooldown(token_id: str, cooldown_until_epoch_seconds: int) -> None:
    """Mark a token as cooling down (e.g. after a rate_limit / billing_error
    response) until the given epoch-seconds timestamp."""
    table = get_claude_teams_token_table_client()
    table.update_item(
        Key={"TokenId": token_id},
        UpdateExpression="SET CooldownUntil = :cooldown_until",
        ExpressionAttributeValues={":cooldown_until": cooldown_until_epoch_seconds},
    )


def delete_token(token_id: str) -> None:
    table = get_claude_teams_token_table_client()
    table.delete_item(Key={"TokenId": token_id})


def pick_next_available_token() -> ClaudeTeamsTokenItem | None:
    """Round-robin selection: among enabled tokens whose cooldown (if any)
    has already passed, pick the one with the oldest `last_used_at` (or one
    that has never been used), mark it as just-used, and return it.

    Returns None if no token is currently available.
    """
    now_ms = get_current_time()
    now_s = now_ms // 1000

    candidates = [
        token
        for token in list_tokens()
        if token.enabled and (token.cooldown_until is None or token.cooldown_until <= now_s)
    ]
    if not candidates:
        return None

    # Round robin: the least-recently-used candidate goes first. A token
    # that has never been used (last_used_at is None) sorts before any
    # that has, so brand-new tokens get tried before recycling old ones.
    candidates.sort(key=lambda t: (t.last_used_at is not None, t.last_used_at or 0))
    picked = candidates[0]

    table = get_claude_teams_token_table_client()
    table.update_item(
        Key={"TokenId": picked.token_id},
        UpdateExpression="SET LastUsedAt = :last_used_at",
        ExpressionAttributeValues={":last_used_at": now_ms},
    )
    picked.last_used_at = now_ms
    return picked
