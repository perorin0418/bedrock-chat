"""DynamoDB-backed repository for hourly Claude Teams usage-limit
snapshots (see `backend/claude_teams_usage_sync/index.py`, the Lambda
that writes these rows).

Two independent, unrelated signals live on each row and must not be
conflated when reading them back:

  - Usage-limit consumption (`FiveHourUtilization` / `SevenDayUtilization`,
    0-100%): a normal, temporary state that resets automatically.
  - Token validity (`FetchStatus`): "auth_error" means the OAuth token
    itself has expired/been revoked (this is the "期限切れ" state), not
    that a usage limit was reached.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal

from boto3.dynamodb.conditions import Key

from app.repositories.common import get_claude_teams_usage_history_table_client

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@dataclass
class ClaudeTeamsUsageHistoryItem:
    token_id: str
    sampled_at_ms: int
    fetch_status: str
    fetch_error_message: str | None = None
    five_hour_utilization: float | None = None
    five_hour_resets_at: str | None = None
    seven_day_utilization: float | None = None
    seven_day_resets_at: str | None = None


def _to_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _item_to_model(item: dict) -> ClaudeTeamsUsageHistoryItem:
    return ClaudeTeamsUsageHistoryItem(
        token_id=item["TokenId"],
        sampled_at_ms=int(item["SampledAtMs"]),
        fetch_status=item.get("FetchStatus", "error"),
        fetch_error_message=item.get("FetchErrorMessage"),
        five_hour_utilization=_to_float(item.get("FiveHourUtilization")),
        five_hour_resets_at=item.get("FiveHourResetsAt"),
        seven_day_utilization=_to_float(item.get("SevenDayUtilization")),
        seven_day_resets_at=item.get("SevenDayResetsAt"),
    )


def list_usage_history(
    token_id: str, since_ms: int, until_ms: int
) -> list[ClaudeTeamsUsageHistoryItem]:
    """Return usage-history snapshots for `token_id` sampled within
    [since_ms, until_ms], oldest first."""
    table = get_claude_teams_usage_history_table_client()
    items: list[dict] = []
    key_condition = Key("TokenId").eq(token_id) & Key("SampledAtMs").between(
        since_ms, until_ms
    )
    last_evaluated_key = None
    while True:
        kwargs = {"KeyConditionExpression": key_condition}
        if last_evaluated_key:
            kwargs["ExclusiveStartKey"] = last_evaluated_key
        response = table.query(**kwargs)
        items.extend(response.get("Items", []))
        last_evaluated_key = response.get("LastEvaluatedKey")
        if not last_evaluated_key:
            break
    return [_item_to_model(item) for item in items]


def get_latest_usage_snapshot(token_id: str) -> ClaudeTeamsUsageHistoryItem | None:
    """Return the most recently sampled usage-history row for `token_id`,
    or None if no snapshot has been recorded yet (e.g. token registered
    less than an hour ago)."""
    table = get_claude_teams_usage_history_table_client()
    response = table.query(
        KeyConditionExpression=Key("TokenId").eq(token_id),
        ScanIndexForward=False,
        Limit=1,
    )
    items = response.get("Items", [])
    if not items:
        return None
    return _item_to_model(items[0])
