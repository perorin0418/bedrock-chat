import logging
from decimal import Decimal as decimal

from app.repositories.common import (
    USAGE_LEDGER_TABLE_NAME,
    get_usage_ledger_table_client,
)
from app.utils import get_current_time
from boto3.dynamodb.conditions import Key

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

USAGE_LEDGER_TTL_SECONDS = 8 * 24 * 60 * 60  # 8 days


def record_usage(user_id: str, price: float) -> None:
    """Record a single message's cost for rate-limit accounting.

    Best-effort: this never raises, so a ledger write failure can't prevent
    the caller's conversation from being persisted.
    """
    if not USAGE_LEDGER_TABLE_NAME:
        logger.warning(
            "USAGE_LEDGER_TABLE_NAME is not set; skipping usage record for "
            f"user {user_id}."
        )
        return

    try:
        now_ms = get_current_time()
        table = get_usage_ledger_table_client(user_id)
        table.put_item(
            Item={
                "PK": user_id,
                "SK": now_ms,
                "Price": decimal(str(price)),
                "expire": now_ms // 1000 + USAGE_LEDGER_TTL_SECONDS,
            }
        )
    except Exception:
        logger.exception(f"Failed to record usage for user {user_id}.")


def get_usage_since(user_id: str, since_ms: int) -> float:
    """Sum the recorded cost for a user from `since_ms` (epoch milliseconds) to now."""
    table = get_usage_ledger_table_client(user_id)

    query_params = {
        "KeyConditionExpression": Key("PK").eq(user_id) & Key("SK").gte(since_ms),
    }
    response = table.query(**query_params)
    total = sum(float(item["Price"]) for item in response["Items"])

    query_count = 1
    MAX_QUERY_COUNT = 5
    while "LastEvaluatedKey" in response:
        query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
        response = table.query(**query_params)
        total += sum(float(item["Price"]) for item in response["Items"])
        query_count += 1
        if query_count > MAX_QUERY_COUNT:
            logger.warning(f"Query count exceeded {MAX_QUERY_COUNT}")
            break

    return total
