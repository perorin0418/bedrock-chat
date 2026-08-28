"""DynamoDB-backed repository for hourly Claude Teams usage-limit
snapshots. Rows are written either by `backend/claude_teams_usage_sync/index.py`
(the hourly Lambda that polls Anthropic's undocumented `/api/oauth/usage`
endpoint on behalf of a full-scope-registered token) or by
`write_usage_snapshot` below (called from the ingest route members' local
usage-reporting scripts push to, for tokens registered without
`user:profile` scope).

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

from app.repositories.common import (
    get_claude_teams_usage_history_table_client,
)
from app.utils import get_current_time

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# How long a usage-history snapshot is kept before DynamoDB TTL-prunes it.
# Matches the retention window used by the hourly sync Lambda so both
# ingest paths behave identically from the CSV export's point of view.
USAGE_HISTORY_TTL_SECONDS = 90 * 24 * 60 * 60


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


def _to_decimal(value) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def write_usage_snapshot(
    token_id: str,
    fetch_status: str,
    fetch_error_message: str | None = None,
    five_hour_utilization: float | None = None,
    five_hour_resets_at: str | None = None,
    seven_day_utilization: float | None = None,
    seven_day_resets_at: str | None = None,
    sampled_at_ms: int | None = None,
) -> None:
    """Append one usage-history snapshot row. Shared by the hourly sync
    Lambda's write path and the usage-snapshot ingest route (members'
    local reporting scripts) so both produce identically-shaped rows for
    the admin screen / CSV export to read back.

    `sampled_at_ms` defaults to now; callers may pass an explicit value
    (e.g. the ingest route uses the time the member's script actually
    queried Anthropic, not when bedrock-chat received the push)."""
    now_ms = sampled_at_ms if sampled_at_ms is not None else get_current_time()
    table = get_claude_teams_usage_history_table_client()

    item = {
        "TokenId": token_id,
        "SampledAtMs": now_ms,
        "FetchStatus": fetch_status,
        "expire": now_ms // 1000 + USAGE_HISTORY_TTL_SECONDS,
    }
    if fetch_error_message is not None:
        item["FetchErrorMessage"] = fetch_error_message

    five_hour_decimal = _to_decimal(five_hour_utilization)
    if five_hour_decimal is not None:
        item["FiveHourUtilization"] = five_hour_decimal
    if five_hour_resets_at is not None:
        item["FiveHourResetsAt"] = five_hour_resets_at

    seven_day_decimal = _to_decimal(seven_day_utilization)
    if seven_day_decimal is not None:
        item["SevenDayUtilization"] = seven_day_decimal
    if seven_day_resets_at is not None:
        item["SevenDayResetsAt"] = seven_day_resets_at

    table.put_item(Item=item)
